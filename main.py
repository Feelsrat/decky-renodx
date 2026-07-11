import decky
import asyncio
import os
import sys
import subprocess
import shutil
import shlex
import signal
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
import json
import glob
import re
import time
import unicodedata
import zipfile
import tarfile
import platform
from datetime import datetime, timezone

# Import new backend modules
try:
    # Decky runs plugins in a sandbox where the current working directory and
    # sys.path are not guaranteed. Ensure this plugin's directory is importable
    # so `import backend...` works reliably.
    _PLUGIN_DIR = Path(__file__).resolve().parent
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))

    from backend.logger import setup_per_game_logger, get_game_log_path
    from backend.manifest import ManifestManager
    from backend.scraper import PCGamingWikiScraper, AntiCheatDetector
    from backend.decision import DecisionTree
    from backend.installer import HDRInstaller
    from backend.cache import PersistentCache
    from backend.pe_imports import imported_dlls, pe_architecture
except ImportError as e:
    decky.logger.error(f"Failed to import backend modules: {e}")
    raise

try:
    import pwd
except ImportError:
    pwd = None

try:
    import ssl
    SSL_AVAILABLE = True
except (ImportError, OSError):
    SSL_AVAILABLE = False

PLUGIN_NAME = "Decky RenoDX"
PLUGIN_PACKAGE = "decky-renodx"
GITHUB_RELEASES_URL = "https://api.github.com/repos/Feelsrat/decky-renodx/releases"
RENODX_MODS_URL = "https://raw.githubusercontent.com/wiki/clshortfuse/renodx/Mods.md"
RESHADE_FXH_URL = "https://raw.githubusercontent.com/crosire/reshade-shaders/slim/Shaders/ReShade.fxh"
RESHADE_MIN_RENODX_VERSION = (6, 7, 3)
SPECIALK_RELEASES_URL = "https://api.github.com/repos/SpecialKO/SpecialK/releases/latest"
LILIUM_HDR_RELEASES_URL = "https://api.github.com/repos/EndlesslyFlowering/ReShade_HDR_shaders/releases/latest"
PUMBO_AUTOHDR_ZIP_URL = "https://github.com/Filoppi/PumboAutoHDR/archive/refs/heads/master.zip"
DISPLAY_COMMANDER_ADDON_NAME = "zzz_display_commander.addon64"
DISPLAY_COMMANDER_ADDON_URL = f"https://github.com/pmnoxx/display-commander/releases/download/latest_build/{DISPLAY_COMMANDER_ADDON_NAME}"
AUTO_CHECK_INTERVAL = 86400
AUTO_UPDATE_CHECK_ON_STARTUP = False
RUNTIME_RELATIVE_PATH = "decky-renodx/reshade"

class Plugin:
    def __init__(self):
        # Some installation/update paths can leave files owned by root.
        # Only attempt ownership repair when running with sufficient privileges
        # (otherwise this can generate confusing permission errors).
        self._fix_deck_user_ownership(Path(getattr(decky, "DECKY_PLUGIN_DIR", ".")))

        deck_user_home = self._deck_user_home()
        deck_user = self._deck_user(deck_user_home)
        xdg_data_home = str(deck_user_home / ".local" / "share")
        runtime_path = os.path.join(xdg_data_home, RUNTIME_RELATIVE_PATH)
        self.environment = {
            'HOME': str(deck_user_home),
            'USER': deck_user,
            'LOGNAME': deck_user,
            'XDG_DATA_HOME': xdg_data_home,
            'MAIN_PATH': runtime_path,
            'UPDATE_RESHADE': '1',
            'MERGE_SHADERS': '1',
            'VULKAN_SUPPORT': '0',
            'GLOBAL_INI': 'ReShade.ini',
            'DELETE_RESHADE_FILES': '0',
            'FORCE_RESHADE_UPDATE_CHECK': '0',
            'RESHADE_ADDON_SUPPORT': '0',
            'RESHADE_VERSION': 'latest',
            'AUTOHDR_ENABLED': '0'
        }
        # Main paths for ReShade
        self.main_path = runtime_path
        self.renodx_import_path = os.path.join(self.environment['XDG_DATA_HOME'], 'decky-renodx', 'imports')
        self.bin_cache_path = os.path.join(self.environment['XDG_DATA_HOME'], 'decky-renodx', 'bin')
        self.manifest_path = os.path.join(self.environment['XDG_DATA_HOME'], 'decky-renodx', 'manifests')
        self.persistent_cache_path = os.path.join(self.environment['XDG_DATA_HOME'], 'decky-renodx', 'cache.json')
        
        # Initialize backend managers
        self.persistent_cache = PersistentCache(self.persistent_cache_path)
        self.manifest_manager = ManifestManager(self.manifest_path)
        self.wiki_scraper = PCGamingWikiScraper(decky.logger)
        self.ac_detector = AntiCheatDetector()
        self.decision_tree = DecisionTree() # Will populate mods later
        self.installer = HDRInstaller(self.manifest_manager)
        
        # Cache for executable paths
        self.executable_cache = {}
        self._last_update_error = ""
        self._cached_update_status: dict[str, Any] | None = None
        self._last_check_time = 0.0
        self._auto_update_task: asyncio.Task[None] | None = None
        self._install_lock = asyncio.Lock()

        self.compat_db_path = os.path.join(self.environment['XDG_DATA_HOME'], 'decky-renodx', 'compatibility.json')
        self.compat_db = self._load_compatibility_db()
        self._compat_update_task = None
        
        # Create necessary directories
        os.makedirs(self.main_path, exist_ok=True)
        os.makedirs(self.renodx_import_path, exist_ok=True)
        os.makedirs(self.bin_cache_path, exist_ok=True)
        os.makedirs(self.manifest_path, exist_ok=True)
        self._migrate_existing_runtime_files()
        self._fix_deck_user_ownership(Path(xdg_data_home) / PLUGIN_PACKAGE)

    def _load_compatibility_db(self) -> dict:
        bundled_path = Path(decky.DECKY_PLUGIN_DIR) / "compatibility.json"
        
        db = {}
        if bundled_path.exists():
            try:
                db.update(json.loads(bundled_path.read_text("utf-8")))
            except Exception as e:
                decky.logger.error(f"Failed to load bundled compatibility.json: {e}")
                
        cached_path = Path(self.compat_db_path)
        if cached_path.exists():
            try:
                db.update(json.loads(cached_path.read_text("utf-8")))
            except Exception as e:
                decky.logger.error(f"Failed to load cached compatibility.json: {e}")
                
        return db

    async def get_game_compatibility_info(self, appid: str) -> dict:
        try:
            if not hasattr(self, "compat_db") or str(appid) not in self.compat_db.get("games", {}):
                return {"status": "success", "has_compat_data": False, "data": {}}
            
            return {
                "status": "success",
                "has_compat_data": True,
                "data": self.compat_db["games"][str(appid)]
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _update_compatibility_db_loop(self):
        url = "https://raw.githubusercontent.com/Feelsrat/decky-renodx/main/compatibility.json"
        while True:
            try:
                content = await asyncio.to_thread(self._fetch_text, url)
                if content:
                    parsed = json.loads(content)
                    # Only accept a payload that looks like a real compat DB so a
                    # bad deploy or truncated response cannot wipe the local copy.
                    if isinstance(parsed, dict) and isinstance(parsed.get("games"), dict) and parsed["games"]:
                        Path(self.compat_db_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(self.compat_db_path).write_text(content, "utf-8")
                        self.compat_db.update(parsed)
                        decky.logger.info("Successfully updated compatibility.json from GitHub (%d games)", len(parsed["games"]))
                    else:
                        decky.logger.warning("Remote compatibility.json payload failed sanity check; keeping local copy.")
            except Exception as e:
                decky.logger.warning(f"Failed to update compatibility.json from GitHub: {e}")
            await asyncio.sleep(86400) # Check once a day

    def _deck_user(self, home: Path | None = None) -> str:
        value = getattr(decky, "DECKY_USER", "") or os.environ.get("SUDO_USER") or ""
        if value and value != "root":
            return value
        if home is not None and home.name and home.name != "root":
            return home.name
        return "deck"

    def _deck_user_home(self) -> Path:
        deck_user_home = getattr(decky, "DECKY_USER_HOME", "")
        if deck_user_home and deck_user_home != "/root":
            return Path(deck_user_home)

        deck_user = getattr(decky, "DECKY_USER", "") or os.environ.get("SUDO_USER") or ""
        if deck_user and deck_user != "root" and pwd is not None:
            try:
                return Path(pwd.getpwnam(deck_user).pw_dir)
            except KeyError:
                pass

        for attr in ["HOME"]:
            value = getattr(decky, attr, "")
            if value and value != "/root":
                return Path(value)
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            return Path("/home") / sudo_user
        return Path("/home/deck")

    def _deck_expanduser(self, path: str) -> str:
        if path == "~":
            return str(self._deck_user_home())
        if path.startswith("~/"):
            return str(self._deck_user_home() / path[2:])
        return os.path.expanduser(path)

    def _fix_deck_user_ownership(self, path: Path | str) -> None:
        # Only root can chown; avoid noisy permission errors when running unprivileged.
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            return
        deck_user = self._deck_user()
        if not deck_user or deck_user == "root":
            return
        target = Path(path)
        if not target.exists():
            return
        try:
            subprocess.run(
                ["chown", "-R", f"{deck_user}:{deck_user}", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as error:
            decky.logger.warning("Could not fix ownership for %s: %s", target, error)

    def _migrate_existing_runtime_files(self) -> None:
        ini_path = Path(self.main_path) / "ReShade.ini"
        if ini_path.exists():
            self._ensure_reshade_tutorial_skipped(ini_path)
            self._ensure_game_relative_shader_paths(ini_path)

    def _get_assets_dir(self) -> Path:
        """Get the assets directory, checking both possible locations"""
        plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        
        # Check defaults/assets first (development)
        defaults_assets = plugin_dir / "defaults" / "assets"
        if defaults_assets.exists():
            decky.logger.info(f"Using assets from: {defaults_assets}")
            return defaults_assets
            
        # Check assets (decky store installation)
        assets = plugin_dir / "assets"
        if assets.exists():
            decky.logger.info(f"Using assets from: {assets}")
            return assets
            
        # Fallback to defaults/assets even if it doesn't exist (for error reporting)
        decky.logger.warning(f"Neither {defaults_assets} nor {assets} exists, defaulting to {defaults_assets}")
        return defaults_assets

    async def _main(self):
        assets_dir = self._get_assets_dir()
        for script in assets_dir.glob("*.sh"):
            script.chmod(0o755)
        self._cleanup_previous_update_artifacts()
        if AUTO_UPDATE_CHECK_ON_STARTUP and self._should_auto_check():
            self._auto_update_task = asyncio.create_task(self._auto_check_update())
        self._compat_update_task = asyncio.create_task(self._update_compatibility_db_loop())
        decky.logger.info("Decky RenoDX loaded")

    async def _unload(self):
        await self._stop_all_tasks()
        decky.logger.info("Decky RenoDX unloaded")

    async def parse_steam_logs_for_executable(self, appid: str) -> dict:
        """Parse Steam console logs to find the exact executable path Steam uses"""
        try:
            decky.logger.info(f"Parsing Steam logs for App ID: {appid}")
            
            # Check cache first
            cache_key = f"steam_log_{appid}"
            if cache_key in self.executable_cache:
                cached_result = self.executable_cache[cache_key]
                # Check if cache is less than 1 hour old
                if time.time() - cached_result.get('timestamp', 0) < 3600:
                    decky.logger.info(f"Using cached result for {appid}")
                    return cached_result
            
            # Steam log file locations
            log_files = [
                "/home/deck/.steam/steam/logs/console-linux.txt",
                "/home/deck/.steam/steam/logs/console_log.txt", 
                "/home/deck/.steam/steam/logs/console_log.previous.txt"
            ]
            
            executable_path = None
            launch_command = None
            
            for log_file in log_files:
                if not os.path.exists(log_file):
                    continue
                    
                decky.logger.info(f"Checking log file: {log_file}")
                
                try:
                    # Read the log file (check last 10000 lines for performance)
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        # Check recent lines first (Steam logs can be large)
                        recent_lines = lines[-10000:] if len(lines) > 10000 else lines
                        
                    # Look for game launch patterns
                    for line in recent_lines:
                        # Pattern 1: Direct executable in launch command
                        # Example: AppId=501300 -- ... '/path/to/game.exe'
                        if f"AppId={appid}" in line and ".exe" in line:
                            # Extract the executable path
                            exe_match = re.search(r"'([^']*\.exe)'", line)
                            if exe_match:
                                potential_exe = exe_match.group(1)
                                # Verify this is a real path and not a temp file
                                if "/steamapps/common/" in potential_exe and os.path.exists(potential_exe):
                                    executable_path = potential_exe
                                    launch_command = line.strip()
                                    decky.logger.info(f"Found executable from logs: {executable_path}")
                                    break
                        
                        # Pattern 2: Game process added/updated logs
                        # Example: Game process added : AppID 501300 "command with exe path"
                        if f"AppID {appid}" in line and (".exe" in line or "Game process" in line):
                            exe_match = re.search(r"'([^']*\.exe)'", line)
                            if not exe_match:
                                # Try different quote patterns
                                exe_match = re.search(r'"([^"]*\.exe)"', line)
                            if exe_match:
                                potential_exe = exe_match.group(1)
                                if "/steamapps/common/" in potential_exe and os.path.exists(potential_exe):
                                    executable_path = potential_exe
                                    launch_command = line.strip()
                                    decky.logger.info(f"Found executable from process log: {executable_path}")
                                    break
                    
                    if executable_path:
                        break
                        
                except Exception as e:
                    decky.logger.error(f"Error reading log file {log_file}: {str(e)}")
                    continue
            
            if executable_path:
                # Cache the result
                result = {
                    "status": "success",
                    "method": "steam_logs",
                    "executable_path": executable_path,
                    "directory_path": os.path.dirname(executable_path),
                    "filename": os.path.basename(executable_path),
                    "launch_command": launch_command,
                    "timestamp": time.time()
                }
                self.executable_cache[cache_key] = result
                
                return result
            else:
                decky.logger.info(f"No executable found in logs for App ID: {appid}")
                return {
                    "status": "not_found",
                    "method": "steam_logs", 
                    "message": "No executable path found in Steam logs"
                }
                
        except Exception as e:
            decky.logger.error(f"Error parsing Steam logs: {str(e)}")
            return {
                "status": "error",
                "method": "steam_logs",
                "message": str(e)
            }

    async def find_game_executable_enhanced(self, appid: str) -> dict:
        """Enhanced executable detection with simplified Linux game detection"""
        try:
            decky.logger.info(f"Enhanced detection with simplified Linux check for App ID: {appid}")
            
            # Get the base game path using existing method
            try:
                steam_root = self._deck_user_home() / ".steam" / "steam"
                library_file = steam_root / "steamapps" / "libraryfolders.vdf"

                if not library_file.exists():
                    return {"status": "error", "message": "Steam library file not found"}

                library_paths = []
                with open(library_file, "r", encoding="utf-8") as file:
                    for line in file:
                        if '"path"' in line:
                            path = line.split('"path"')[1].strip().strip('"').replace("\\\\", "/")
                            library_paths.append(path)

                base_game_path = None
                game_name = None
                for library_path in library_paths:
                    manifest_path = Path(library_path) / "steamapps" / f"appmanifest_{appid}.acf"
                    if manifest_path.exists():
                        with open(manifest_path, "r", encoding="utf-8") as manifest:
                            manifest_content = manifest.read()
                            for line in manifest_content.split('\n'):
                                if '"installdir"' in line:
                                    install_dir = line.split('"installdir"')[1].strip().strip('"')
                                    base_game_path = str(Path(library_path) / "steamapps" / "common" / install_dir)
                                    game_name = install_dir
                                elif '"name"' in line:
                                    game_title = line.split('"name"')[1].strip().strip('"')
                                    if not game_name:
                                        game_name = game_title
                        break

                if not base_game_path:
                    return {"status": "error", "message": f"Could not find installation directory for AppID: {appid}"}
                    
                decky.logger.info(f"Base game path: {base_game_path}")
                decky.logger.info(f"Game name from Steam: {game_name}")
                
                # Check appmanifest for Linux indicators
                manifest_has_linux = False
                for library_path in library_paths:
                    manifest_path = Path(library_path) / "steamapps" / f"appmanifest_{appid}.acf"
                    if manifest_path.exists():
                        with open(manifest_path, "r", encoding="utf-8") as manifest:
                            manifest_content = manifest.read().lower()
                            if "linux" in manifest_content:
                                manifest_has_linux = True
                                decky.logger.info("Found 'linux' in appmanifest")
                                break
                
            except Exception as e:
                return {"status": "error", "message": str(e)}
            
            game_path_obj = Path(base_game_path)
            if not game_path_obj.exists():
                return {"status": "error", "message": f"Game path not found: {base_game_path}"}
            
            # Simplified Linux detection - only check for key indicators
            linux_indicators = {
                'so_files': [],
                'sh_files': []
            }
            
            all_executables = []
            
            decky.logger.info(f"Scanning directory tree for executables and Linux indicators: {base_game_path}")
            
            # Single directory traversal
            for root, dirs, files in os.walk(base_game_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_obj = Path(file_path)
                    rel_path = os.path.relpath(file_path, base_game_path)
                    
                    try:
                        file_size = os.path.getsize(file_path)
                    except:
                        continue
                    
                    # Check for Windows executables
                    if file.lower().endswith('.exe'):
                        all_executables.append({
                            "path": file_path,
                            "directory_path": os.path.dirname(file_path),
                            "relative_path": rel_path,
                            "filename": file,
                            "size": file_size,
                            "size_mb": round(file_size / (1024 * 1024), 1),
                            "type": "windows_exe"
                        })
                        decky.logger.debug(f"Found Windows exe: {file} ({rel_path}) - {round(file_size / (1024 * 1024), 1)}MB")
                    
                    # Simplified Linux detection - only .so and .sh files
                    file_lower = file.lower()
                    
                    # Check for .so files
                    if file_lower.endswith('.so') or '.so.' in file_lower:
                        linux_indicators['so_files'].append(rel_path)
                        decky.logger.debug(f"Found .so file: {rel_path}")
                    
                    # Check for .sh files
                    elif file_lower.endswith('.sh'):
                        linux_indicators['sh_files'].append(rel_path)
                        decky.logger.debug(f"Found .sh file: {rel_path}")
            
            # Filter out utility executables from Windows list
            skip_name_tokens = [
                "unins", "redist", "vcredist", "directx", "setup", "install",
                "crashreport", "crashpad", "epicwebhelper", "eac", "easyanticheat",
                "ue4prereq", "ueprereq", "dotnet", "oalinst",
            ]
            main_windows_executables = []
            for exe in all_executables:
                if exe["type"] == "windows_exe":
                    exe_name = exe["filename"].lower()
                    rel_lower = exe["relative_path"].lower().replace("\\", "/")
                    # Unreal Engine ships editor/support tools under Engine/;
                    # the real game binary lives in <Game>/Binaries/Win64.
                    if rel_lower.startswith("engine/") or "/engine/" in rel_lower:
                        continue
                    if not any(skip in exe_name for skip in skip_name_tokens):
                        main_windows_executables.append(exe)
            
            # Simplified Linux game determination
            is_linux_game = False
            linux_confidence = "low"
            linux_reasons = []
            
            # Check for Linux indicators
            so_file_count = len(linux_indicators['so_files'])
            sh_file_count = len(linux_indicators['sh_files'])
            
            if manifest_has_linux:
                is_linux_game = True
                linux_confidence = "high"
                linux_reasons.append("Steam manifest contains 'linux'")
            
            if so_file_count >= 5:  # Multiple .so files is a strong indicator
                is_linux_game = True
                if linux_confidence != "high":
                    linux_confidence = "medium"
                linux_reasons.append(f"Found {so_file_count} shared library (.so) files")
            
            if sh_file_count >= 2:  # Multiple shell scripts
                is_linux_game = True
                if linux_confidence == "low":
                    linux_confidence = "medium"
                linux_reasons.append(f"Found {sh_file_count} shell script (.sh) files")
            
            # If no Windows executables and Linux indicators present
            if not main_windows_executables and (so_file_count > 0 or sh_file_count > 0):
                is_linux_game = True
                if linux_confidence == "low":
                    linux_confidence = "medium"
                linux_reasons.append("No Windows executables found, Linux files present")
            
            # If it's determined to be a Linux game, return early with warning
            if is_linux_game and linux_confidence in ["high", "medium"]:
                return {
                    "status": "linux_game_detected",
                    "method": "enhanced_detection_with_simplified_linux_check",
                    "is_linux_game": True,
                    "linux_confidence": linux_confidence,
                    "linux_reasons": linux_reasons,
                    "linux_indicators": linux_indicators,
                    "windows_executables_found": len(main_windows_executables),
                    "message": "Linux version detected - ReShade requires Windows version through Proton",
                    "details": {
                        "game_path": base_game_path,
                        "total_files_scanned": len(all_executables),
                        "windows_exe_count": len(main_windows_executables),
                        "so_files_count": so_file_count,
                        "sh_files_count": sh_file_count
                    },
                    "scan_summary": {
                        "total_files_scanned": len(all_executables),
                        "windows_executables": len(all_executables),
                        "main_windows_executables": len(main_windows_executables),
                        "so_files": so_file_count,
                        "sh_files": sh_file_count,
                        "linux_indicators_found": so_file_count + sh_file_count
                    }
                }
            
            # Continue with Windows executable analysis if not a Linux game
            if not main_windows_executables:
                return {
                    "status": "error",
                    "method": "enhanced_detection_with_simplified_linux_check",
                    "is_linux_game": is_linux_game,
                    "linux_confidence": linux_confidence,
                    "message": f"No suitable Windows executables found in game directory: {base_game_path}",
                    "details": {
                        "total_executables_found": len(all_executables),
                        "windows_exe_count": len(all_executables),
                        "main_windows_exe_count": len(main_windows_executables),
                        "so_files_count": so_file_count,
                        "sh_files_count": sh_file_count
                    },
                    "scan_summary": {
                        "total_files_scanned": len(all_executables),
                        "windows_executables": len(all_executables),
                        "main_windows_executables": len(main_windows_executables),
                        "so_files": so_file_count,
                        "sh_files": sh_file_count,
                        "linux_indicators_found": so_file_count + sh_file_count
                    }
                }
            
            decky.logger.info(f"Found {len(main_windows_executables)} Windows executables for scoring")
            
            # ENHANCED SCORING for Windows executables (keeping existing logic)
            def score_executable(exe_info):
                score = 50
                filename = exe_info["filename"].lower()
                filename_no_ext = os.path.splitext(filename)[0]
                rel_path = exe_info["relative_path"].lower()
                size_mb = exe_info["size_mb"]
                
                decky.logger.debug(f"Scoring {filename} at {rel_path}")
                
                # Enhanced game name matching with multiple normalization approaches
                clean_game_name = re.sub(r'[^a-z0-9]', '', game_name.lower()) if game_name else ""
                clean_filename = re.sub(r'[^a-z0-9]', '', filename_no_ext)
                
                # Split into words for more flexible matching
                game_name_words = re.findall(r'[a-z0-9]+', game_name.lower()) if game_name else []
                filename_words = re.findall(r'[a-z0-9]+', filename_no_ext)
                
                # Calculate various types of matches
                name_match_score = 0
                
                # Exact matches (highest priority)
                if clean_filename == clean_game_name:
                    name_match_score += 60
                    decky.logger.debug(f"  Exact name match: +60 (normalized names match exactly)")
                
                # Substantial partial matches (high priority)
                elif clean_game_name and (clean_game_name in clean_filename or clean_filename in clean_game_name):
                    # Calculate how much of the string matches
                    match_ratio = max(
                        len(clean_game_name) / len(clean_filename) if len(clean_filename) > 0 else 0,
                        len(clean_filename) / len(clean_game_name) if len(clean_game_name) > 0 else 0
                    )
                    # Scale the score based on how much of the string matches (max 45 points)
                    partial_score = min(45, int(match_ratio * 45))
                    name_match_score += partial_score
                    decky.logger.debug(f"  Partial name match: +{partial_score} (ratio: {match_ratio:.2f})")
                
                # Word-level matches (medium priority)
                else:
                    # Find matching words between game name and filename
                    matching_words = set(game_name_words).intersection(set(filename_words))
                    
                    if matching_words:
                        # Calculate match percentage relative to the source words
                        match_percentage = len(matching_words) / len(game_name_words) if game_name_words else 0
                        word_score = len(matching_words) * 8 * (1 + match_percentage)  # Scale based on percentage match
                        name_match_score += min(40, round(word_score))  # Cap at 40 points
                        decky.logger.debug(f"  Word match: +{min(40, round(word_score))} ({matching_words})")
                
                # Common game executable names bonus
                if any(common in filename_no_ext.lower() for common in ["game", "main", "client", "app", "play"]):
                    common_bonus = 15
                    name_match_score += common_bonus
                    decky.logger.debug(f"  Common game exe name: +{common_bonus}")
                
                # Add the name match score to the total score
                score += name_match_score
                
                # Size-based scoring (reduced weights)
                size_score = 0
                if size_mb > 50:      # Large games
                    size_score = 10  # Reduced from 35
                elif size_mb > 20:    # Medium games  
                    size_score = 8   # Reduced from 25
                elif size_mb > 5:     # Small games
                    size_score = 5   # Reduced from 15
                elif size_mb > 1:     # Small but not tiny
                    size_score = 2   # Reduced from 5
                elif size_mb < 0.5:   # Very small files (likely utilities)
                    size_score = -20  # Keep this penalty to avoid tiny utility executables
                
                score += size_score
                decky.logger.debug(f"  Size score: +{size_score} ({size_mb} MB)")
                
                # Path-based scoring (more moderate)
                path_score = 0
                # Unity: the real game exe always sits next to UnityPlayer.dll.
                if os.path.exists(os.path.join(exe_info["directory_path"], "UnityPlayer.dll")):
                    path_score += 20
                if any(marker in rel_path for marker in ["binaries/win64", "binaries\\win64", "binaries/wingdk", "binaries\\wingdk"]):    # Unreal Engine pattern
                    path_score += 15  # Reduced from 25
                elif "bin" in rel_path:             # Common bin directory
                    path_score += 10  # Reduced from 15
                elif "game" in rel_path:            # Game subdirectory
                    path_score += 8   # Reduced from 10
                elif rel_path.count("/") == 0 and rel_path.count("\\") == 0:  # Root directory
                    path_score += 5   # Reduced from 8
                
                score += path_score
                decky.logger.debug(f"  Path score: +{path_score}")
                
                # Special patterns from real data (more moderate)
                special_score = 0
                if "shipping" in filename:          # Unreal shipping builds
                    special_score += 15  # Reduced from 20
                elif "win64" in filename:           # 64-bit indicator
                    special_score += 5   # Reduced from 8
                elif "launcher" in filename:        # Launchers (lower score but don't exclude)
                    special_score -= 25  # Increased penalty from 15
                
                score += special_score
                if special_score != 0:
                    decky.logger.debug(f"  Special pattern score: {special_score}")
                
                # Moderate penalty for deep nesting
                path_depth = rel_path.count("/") + rel_path.count("\\")
                if path_depth > 4:  # Increased threshold
                    depth_penalty = (path_depth - 4) * 3
                    score -= depth_penalty
                    decky.logger.debug(f"  Deep nesting penalty: -{depth_penalty}")
                
                # Cap score between 0 and 100
                score = max(0, min(100, score))
                
                # Round to 1 decimal place for cleaner display
                score = round(score, 1)
                
                decky.logger.debug(f"  Final score for {filename}: {score} (name match: {name_match_score})")
                return score
            
            # Score all Windows executables
            scored_executables = []
            for exe_info in main_windows_executables:
                score = score_executable(exe_info)
                if score > 0:
                    scored_executables.append({
                        **exe_info,
                        "score": score
                    })
                else:
                    decky.logger.debug(f"Filtered out {exe_info['filename']} with score {score}")
            
            if not scored_executables:
                return {
                    "status": "error",
                    "method": "enhanced_detection_with_simplified_linux_check",
                    "is_linux_game": is_linux_game,
                    "message": "No suitable Windows executables found after scoring",
                    "scan_summary": {
                        "total_files_scanned": len(all_executables),
                        "windows_executables": len(all_executables),
                        "main_windows_executables": len(main_windows_executables),
                        "so_files": so_file_count,
                        "sh_files": sh_file_count,
                        "linux_indicators_found": so_file_count + sh_file_count
                    }
                }
            
            # Sort and get top results
            scored_executables.sort(key=lambda x: x["score"], reverse=True)
            top_executables = scored_executables[:5]
            best_executable = top_executables[0]
            
            decky.logger.info(f"Top executable: {best_executable['filename']} (score: {best_executable['score']})")
            
            return {
                "status": "success",
                "method": "enhanced_detection_with_simplified_linux_check",
                "executable_path": best_executable["path"],
                "directory_path": best_executable["directory_path"],
                "filename": best_executable["filename"],
                "all_executables": top_executables,
                "confidence": "high" if best_executable["score"] > 70 else "medium",
                "is_linux_game": is_linux_game,
                "linux_confidence": linux_confidence,
                "linux_reasons": linux_reasons if linux_reasons else None,
                "scan_summary": {
                    "total_files_scanned": len(all_executables),
                    "windows_executables": len(all_executables),
                    "main_windows_executables": len(main_windows_executables),
                    "so_files": so_file_count,
                    "sh_files": sh_file_count,
                    "linux_indicators_found": so_file_count + sh_file_count
                }
            }
            
        except Exception as e:
            decky.logger.error(f"Enhanced detection error: {str(e)}")
            return {
                "status": "error",
                "method": "enhanced_detection_with_simplified_linux_check",
                "message": str(e)
            }

    async def find_game_executable_path(self, appid: str) -> dict:
        """
        Primary method that runs BOTH Steam logs and enhanced detection, returning both results
        """
        try:
            decky.logger.info(f"Finding executable path for App ID: {appid}")
            
            # Method 1: Steam console logs
            steam_logs_result = await self.parse_steam_logs_for_executable(appid)
            
            # Method 2: Enhanced detection (now includes Linux detection)
            enhanced_result = await self.find_game_executable_enhanced(appid)
            
            # Handle special case where enhanced detection found a Linux game
            if enhanced_result.get("status") == "linux_game_detected":
                # Return the Linux detection as the enhanced result
                return {
                    "status": "success", 
                    "steam_logs_result": steam_logs_result,
                    "enhanced_detection_result": enhanced_result,
                    "recommended_method": "enhanced_detection",  # Linux detection takes priority
                    "linux_game_warning": True
                }
            
            # Determine recommended method for Windows games
            recommended_method = "steam_logs"
            if steam_logs_result["status"] != "success":
                recommended_method = "enhanced_detection"
            
            return {
                "status": "success",
                "steam_logs_result": steam_logs_result,
                "enhanced_detection_result": enhanced_result,
                "recommended_method": recommended_method
            }
            
        except Exception as e:
            decky.logger.error(f"Error in find_game_executable_path: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def save_shader_preferences(self, selected_shaders: list) -> dict:
        """Save user's shader preferences to a file"""
        try:
            preferences_file = os.path.join(self.main_path, "user_preferences.json")
            
            # Load existing preferences to preserve other settings
            existing_preferences = {}
            if os.path.exists(preferences_file):
                try:
                    with open(preferences_file, 'r') as f:
                        existing_preferences = json.load(f)
                except:
                    pass  # If file is corrupted, start fresh
            
            # Update shader preferences while preserving other settings
            existing_preferences.update({
                "selected_shaders": selected_shaders,
                "last_updated": int(time.time()),
                "version": "1.1"
            })
            
            # Ensure directory exists
            os.makedirs(self.main_path, exist_ok=True)
            
            with open(preferences_file, 'w') as f:
                json.dump(existing_preferences, f, indent=2)
            self._fix_deck_user_ownership(Path(self.main_path))
            
            decky.logger.info(f"Saved shader preferences: {selected_shaders}")
            return {"status": "success", "message": "Shader preferences saved successfully"}
            
        except Exception as e:
            decky.logger.error(f"Error saving shader preferences: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def load_shader_preferences(self) -> dict:
        """Load user's shader preferences from file"""
        try:
            preferences_file = os.path.join(self.main_path, "user_preferences.json")
            
            # Also check old file for migration
            old_preferences_file = os.path.join(self.main_path, "shader_preferences.json")
            
            preferences = None
            
            # Try to load from new file first
            if os.path.exists(preferences_file):
                with open(preferences_file, 'r') as f:
                    preferences = json.load(f)
            # Migrate from old file if exists
            elif os.path.exists(old_preferences_file):
                with open(old_preferences_file, 'r') as f:
                    old_prefs = json.load(f)
                    # Migrate to new format
                    preferences = {
                        "selected_shaders": old_prefs.get("selected_shaders", []),
                        "last_updated": old_prefs.get("last_updated", int(time.time())),
                        "version": "1.1",
                        "autohdr_enabled": False  # Default for migrated preferences
                    }
                    # Save in new format and remove old file
                    with open(preferences_file, 'w') as f:
                        json.dump(preferences, f, indent=2)
                    try:
                        os.remove(old_preferences_file)
                    except:
                        pass
            
            if not preferences:
                return {"status": "success", "preferences": None, "message": "No preferences file found"}
            
            # Validate the preferences structure
            if "selected_shaders" not in preferences:
                return {"status": "error", "message": "Invalid preferences file format"}
            
            decky.logger.info(f"Loaded shader preferences: {preferences['selected_shaders']}")
            return {
                "status": "success", 
                "preferences": preferences,
                "selected_shaders": preferences["selected_shaders"]
            }
            
        except Exception as e:
            decky.logger.error(f"Error loading shader preferences: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def has_shader_preferences(self) -> dict:
        """Check if user has saved shader preferences"""
        try:
            preferences_file = os.path.join(self.main_path, "user_preferences.json")
            old_preferences_file = os.path.join(self.main_path, "shader_preferences.json")
            
            exists = os.path.exists(preferences_file) or os.path.exists(old_preferences_file)
            
            if exists:
                # Also load and return a summary
                result = await self.load_shader_preferences()
                if result["status"] == "success" and result["preferences"]:
                    shader_count = len(result["selected_shaders"])
                    return {
                        "status": "success",
                        "has_preferences": True,
                        "shader_count": shader_count,
                        "last_updated": result["preferences"].get("last_updated", 0)
                    }
            
            return {"status": "success", "has_preferences": False}
            
        except Exception as e:
            decky.logger.error(f"Error checking shader preferences: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def save_autohdr_preference(self, autohdr_enabled: bool) -> dict:
        """Save user's AutoHDR preference"""
        try:
            preferences_file = os.path.join(self.main_path, "user_preferences.json")
            
            # Load existing preferences to preserve other settings
            existing_preferences = {}
            if os.path.exists(preferences_file):
                try:
                    with open(preferences_file, 'r') as f:
                        existing_preferences = json.load(f)
                except:
                    pass  # If file is corrupted, start fresh
            
            # Update AutoHDR preference while preserving other settings
            existing_preferences.update({
                "autohdr_enabled": autohdr_enabled,
                "last_updated": int(time.time()),
                "version": "1.1"
            })
            
            # Ensure selected_shaders exists if it doesn't
            if "selected_shaders" not in existing_preferences:
                existing_preferences["selected_shaders"] = []
            
            # Ensure directory exists
            os.makedirs(self.main_path, exist_ok=True)
            
            with open(preferences_file, 'w') as f:
                json.dump(existing_preferences, f, indent=2)
            
            decky.logger.info(f"Saved AutoHDR preference: {autohdr_enabled}")
            return {"status": "success", "message": "AutoHDR preference saved successfully"}
            
        except Exception as e:
            decky.logger.error(f"Error saving AutoHDR preference: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def load_autohdr_preference(self) -> dict:
        """Load user's AutoHDR preference"""
        try:
            preferences_file = os.path.join(self.main_path, "user_preferences.json")
            
            if not os.path.exists(preferences_file):
                return {"status": "success", "autohdr_enabled": False, "message": "No preferences file found"}
            
            with open(preferences_file, 'r') as f:
                preferences = json.load(f)
            
            autohdr_enabled = preferences.get("autohdr_enabled", False)
            
            decky.logger.info(f"Loaded AutoHDR preference: {autohdr_enabled}")
            return {
                "status": "success", 
                "autohdr_enabled": autohdr_enabled
            }
            
        except Exception as e:
            decky.logger.error(f"Error loading AutoHDR preference: {str(e)}")
            return {"status": "error", "message": str(e), "autohdr_enabled": False}

    async def save_installed_configuration(self, with_addon: bool, version: str, with_autohdr: bool, selected_shaders: list) -> dict:
        """Save the configuration that was actually installed"""
        try:
            installed_config = self._write_installed_configuration_sync(with_addon, version, with_autohdr, selected_shaders)
            self._fix_deck_user_ownership(Path(self.main_path))
            decky.logger.info(f"Saved installed configuration: {installed_config}")
            return {"status": "success"}
            
        except Exception as e:
            decky.logger.error(f"Error saving installed configuration: {str(e)}")
            return {"status": "error", "message": str(e)}

    def _write_installed_configuration_sync(self, with_addon: bool, version: str, with_autohdr: bool, selected_shaders: list) -> dict:
        config_file = os.path.join(self.main_path, "installed_config.json")
        installed_config = {
            "with_addon": with_addon,
            "version": version,
            "with_autohdr": with_autohdr,
            "selected_shaders": selected_shaders or [],
            "installed_at": int(time.time()),
        }
        os.makedirs(self.main_path, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(installed_config, f, indent=2)
        return installed_config

    async def load_installed_configuration(self) -> dict:
        """Load the configuration that was actually installed"""
        try:
            config_file = os.path.join(self.main_path, "installed_config.json")
            
            if not os.path.exists(config_file):
                return {"status": "success", "config": None}
            
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            return {"status": "success", "config": config}
            
        except Exception as e:
            decky.logger.error(f"Error loading installed configuration: {str(e)}")
            return {"status": "error", "message": str(e), "config": None}

    async def clear_installed_configuration(self) -> dict:
        """Clear the installed configuration (called on uninstall)"""
        try:
            config_file = os.path.join(self.main_path, "installed_config.json")
            
            if os.path.exists(config_file):
                os.remove(config_file)
            
            return {"status": "success"}
            
        except Exception as e:
            decky.logger.error(f"Error clearing installed configuration: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def get_available_shaders(self) -> dict:
        """HDR-only fork: broad ReShade shader package selection is disabled."""
        return {"status": "success", "shaders": [], "total_count": 0}

    async def detect_steam_deck_model(self) -> dict:
        """Detect Steam Deck model (OLED vs LCD) using board name"""
        try:
            decky.logger.info("Detecting Steam Deck model...")
            
            # First check if we can read system info at all
            is_steam_deck = False
            product_name = ""
            
            try:
                with open('/sys/devices/virtual/dmi/id/product_name', 'r') as f:
                    product_name = f.read().strip()
                decky.logger.info(f"DMI Product name: '{product_name}'")
                
                # More flexible Steam Deck detection
                if any(term in product_name.lower() for term in ["steam deck", "steamdeck", "jupiter", "galileo"]):
                    is_steam_deck = True
                    decky.logger.info("Confirmed this is a Steam Deck")
                else:
                    decky.logger.warning(f"Product name '{product_name}' doesn't indicate Steam Deck")
            except (FileNotFoundError, PermissionError) as e:
                decky.logger.warning(f"Could not read DMI product name: {e}")
            
            # If we can't confirm it's a Steam Deck through product name, 
            # let's assume it is and try board detection anyway
            if not is_steam_deck:
                decky.logger.info("Could not confirm Steam Deck via product name, proceeding with board detection")
            
            # Check board name - most reliable method for Steam Deck OLED vs LCD
            board_name = ""
            try:
                with open('/sys/devices/virtual/dmi/id/board_name', 'r') as f:
                    board_name = f.read().strip()
                decky.logger.info(f"DMI Board name: '{board_name}'")
                
                # Check for OLED (Galileo)
                if "Galileo" in board_name:
                    decky.logger.info("Detected Steam Deck OLED (Galileo)")
                    return {
                        "status": "success",
                        "model": "OLED",
                        "is_oled": True
                    }
                # Check for LCD (Jupiter)
                elif "Jupiter" in board_name:
                    decky.logger.info("Detected Steam Deck LCD (Jupiter)")
                    return {
                        "status": "success",
                        "model": "LCD",
                        "is_oled": False
                    }
                else:
                    decky.logger.warning(f"Unknown board name: '{board_name}'")
                    
                    # If we confirmed it's a Steam Deck but unknown board, default to LCD
                    if is_steam_deck:
                        decky.logger.info("Confirmed Steam Deck but unknown board, defaulting to LCD")
                        return {
                            "status": "success",
                            "model": "LCD",
                            "is_oled": False
                        }
                    
            except (FileNotFoundError, PermissionError) as e:
                decky.logger.warning(f"Could not read DMI board name: {e}")
            
            # Additional fallback checks for Steam Deck detection
            try:
                # Check system manufacturer
                with open('/sys/devices/virtual/dmi/id/sys_vendor', 'r') as f:
                    vendor = f.read().strip()
                decky.logger.info(f"System vendor: '{vendor}'")
                
                if "Valve" in vendor:
                    is_steam_deck = True
                    decky.logger.info("Confirmed Steam Deck via vendor")
            except (FileNotFoundError, PermissionError) as e:
                decky.logger.debug(f"Could not read sys_vendor: {e}")
            
            # Final decision logic
            if is_steam_deck:
                # We know it's a Steam Deck but couldn't determine the model
                decky.logger.info("Confirmed Steam Deck, but model detection failed - defaulting to LCD")
                return {
                    "status": "success",
                    "model": "LCD", 
                    "is_oled": False
                }
            else:
                # We couldn't confirm this is a Steam Deck
                decky.logger.info("Could not confirm this is a Steam Deck")
                return {
                    "status": "success",
                    "model": "Not Steam Deck",
                    "is_oled": False
                }
                
        except Exception as e:
            decky.logger.error(f"Error detecting Steam Deck model: {str(e)}")
            return {
                "status": "error", 
                "message": str(e),
                "model": "Unknown",
                "is_oled": False
            }

    async def check_reshade_path(self) -> dict:
        path = Path(self.main_path)
        marker_file = path / ".installed"
        addon_marker = path / ".installed_addon"
        
        # Check version information
        version_info = {"version": "unknown", "addon": False}
        if marker_file.exists() or addon_marker.exists():
            try:
                version_file = path / "reshade" / "LVERS"
                if version_file.exists():
                    with open(version_file, 'r') as f:
                        version_content = f.read().strip()
                        if "last" in version_content.lower():
                            version_info["version"] = "last"
                        else:
                            version_info["version"] = "latest"
                        version_info["addon"] = "addon" in version_content.lower()
            except Exception as e:
                decky.logger.error(f"Error reading version info: {str(e)}")
        
        return {
            "exists": marker_file.exists() or addon_marker.exists(),
            "is_addon": addon_marker.exists(),
            "version_info": version_info
        }

    async def run_install_reshade(self, with_addon: bool = False, version: str = "latest", with_autohdr: bool = False, selected_shaders: list = None) -> dict:
        try:
            install_description = f"Installing ReShade {version}"
            if with_addon:
                install_description += " with addon support"
            if with_autohdr:
                install_description += " and AutoHDR components"
            install_description += " with HDR-only payload"

            decky.logger.info(install_description)
            await asyncio.wait_for(
                asyncio.to_thread(self._install_hdr_runtime_sync, version, with_addon, with_autohdr),
                timeout=360,
            )

            return {"status": "success", "output": self._runtime_install_success_message(version, with_addon, with_autohdr)}
        except asyncio.TimeoutError:
            message = "HDR runtime install timed out after 6 minutes. Check network/download state, then try again."
            decky.logger.error(message)
            return {"status": "error", "message": message}
        except Exception as e:
            decky.logger.error(f"Install error: {str(e)}")
            return {"status": "error", "message": str(e)}

    def _install_hdr_runtime_sync(self, version: str, with_addon: bool, with_autohdr: bool) -> None:
        asset_result = self._ensure_runtime_assets()
        if not asset_result["ok"]:
            raise RuntimeError(asset_result["message"])

        self._install_hdr_runtime_python(version)

        if with_addon:
            marker_file = Path(self.main_path) / ".installed_addon"
            normal_marker = Path(self.main_path) / ".installed"
            if normal_marker.exists():
                normal_marker.unlink()
        else:
            marker_file = Path(self.main_path) / ".installed"
            addon_marker = Path(self.main_path) / ".installed_addon"
            if addon_marker.exists():
                addon_marker.unlink()

        marker_file.touch()
        self._write_installed_configuration_sync(with_addon, version, with_autohdr, ["autohdr"])
        self._fix_deck_user_ownership(Path(self.main_path))
        self.executable_cache.clear()

    def _runtime_install_success_message(self, version: str, with_addon: bool, with_autohdr: bool) -> str:
        version_display = f"ReShade {version.title()}"
        if with_addon:
            version_display += " (with Addon Support)"
        if with_autohdr:
            version_display += " and AutoHDR components"
        version_display += " with HDR-only payload"
        return f"{version_display} installed successfully!"

    async def run_uninstall_reshade(self) -> dict:
        try:
            assets_dir = self._get_assets_dir()
            script_path = assets_dir / "reshade-uninstall.sh"
            
            if not script_path.exists():
                return {"status": "error", "message": "Uninstall script not found"}

            # Create environment with required LD_LIBRARY_PATH fix for Decky v3.1.10+
            clean_env = {**os.environ, **self.environment}
            clean_env["SEVENZIP"] = str(Path(self.bin_cache_path) / "7zz")
            clean_env["LD_LIBRARY_PATH"] = ""
            
            process = subprocess.run(
                ["/bin/bash", str(script_path)],
                cwd=str(assets_dir),
                env=clean_env,
                capture_output=True,
                text=True
            )
            
            if process.returncode != 0:
                return {"status": "error", "message": process.stderr}

            # Remove installation markers
            marker_file = Path(self.main_path) / ".installed"
            addon_marker = Path(self.main_path) / ".installed_addon"
            if marker_file.exists():
                marker_file.unlink()
            if addon_marker.exists():
                addon_marker.unlink()

            # Clear installed configuration and cache
            await self.clear_installed_configuration()
            self.executable_cache.clear()
                
            return {"status": "success", "output": "ReShade uninstalled"}
        except Exception as e:
            decky.logger.error(str(e))
            return {"status": "error", "message": str(e)}

    def _ensure_runtime_assets(self) -> dict[str, Any]:
        try:
            bin_dir = Path(self.bin_cache_path)
            bin_dir.mkdir(parents=True, exist_ok=True)

            reshade_target = bin_dir / "reshade_latest_addon.exe"
            reshade_version_file = bin_dir / "reshade_latest_addon.version"
            if not reshade_target.exists() or reshade_target.stat().st_size < 1024 * 1024 or not self._reshade_installer_supports_renodx(reshade_target, reshade_version_file):
                url = self._latest_reshade_addon_url()
                self._download_url(url, reshade_target)
                reshade_version_file.write_text(".".join(map(str, self._reshade_version_from_name(url))), encoding="utf-8")
                if not self._reshade_installer_supports_renodx(reshade_target, reshade_version_file):
                    raise RuntimeError("Downloaded ReShade add-on runtime is too old for current RenoDX addons.")
            last_target = bin_dir / "reshade_last_addon.exe"
            if not last_target.exists():
                shutil.copy2(reshade_target, last_target)

            sevenzip = bin_dir / "7zz"
            if not sevenzip.exists():
                self._download_7zip_binary(bin_dir)

            autohdr_archive = bin_dir / "autohdr_addon.tar.gz"
            advanced_archive = bin_dir / "advanced_autohdr_effect.tar.gz"
            if not autohdr_archive.exists() or not advanced_archive.exists():
                self._download_autohdr_payloads(bin_dir, autohdr_archive, advanced_archive)
            reshade_fxh = bin_dir / "ReShade.fxh"
            if not reshade_fxh.exists() or reshade_fxh.stat().st_size < 1024:
                self._download_url(RESHADE_FXH_URL, reshade_fxh)
            specialk_archive = bin_dir / "SpecialK.7z"
            if not specialk_archive.exists() or specialk_archive.stat().st_size < 1024 * 1024:
                self._download_latest_github_asset(SPECIALK_RELEASES_URL, specialk_archive, [".7z", ".zip"])
            lilium_archive = bin_dir / "lilium_hdr_shaders.7z"
            if not lilium_archive.exists() or lilium_archive.stat().st_size < 1024:
                self._download_latest_github_asset(LILIUM_HDR_RELEASES_URL, lilium_archive, [".7z", ".zip"])
            pumbo_archive = bin_dir / "PumboAutoHDR-master.zip"
            if not pumbo_archive.exists() or pumbo_archive.stat().st_size < 1024:
                self._download_url(PUMBO_AUTOHDR_ZIP_URL, pumbo_archive)

            return {"ok": True, "message": "Runtime assets are ready."}
        except Exception as error:
            decky.logger.exception("Failed to prepare runtime assets")
            return {"ok": False, "message": f"Could not download HDR runtime assets: {error}"}

    def _install_hdr_runtime_python(self, version: str) -> None:
        bin_dir = Path(self.bin_cache_path)
        main_path = Path(self.main_path)
        reshade_path = main_path / "reshade"
        reshade_path.mkdir(parents=True, exist_ok=True)
        (main_path / "ReShade_shaders" / "Merged" / "Shaders").mkdir(parents=True, exist_ok=True)
        (main_path / "ReShade_shaders" / "Merged" / "Textures").mkdir(parents=True, exist_ok=True)
        (main_path / "AutoHDR_addons").mkdir(parents=True, exist_ok=True)

        installer = bin_dir / ("reshade_last_addon.exe" if version == "last" else "reshade_latest_addon.exe")
        sevenzip = bin_dir / "7zz"
        if not installer.exists():
            raise FileNotFoundError(f"Missing ReShade installer: {installer}")
        if not sevenzip.exists():
            raise FileNotFoundError(f"Missing private 7-Zip extractor: {sevenzip}")

        version_suffix = "_last_Addon" if version == "last" else "_latest_Addon"
        target_dir = reshade_path / f"{version}{version_suffix}"
        with tempfile.TemporaryDirectory(prefix=f"{PLUGIN_PACKAGE}-reshade-") as temp_root:
            result = subprocess.run(
                [str(sevenzip), "-y", "e", str(installer)],
                cwd=temp_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not extract ReShade installer: {result.stderr.strip() or result.stdout.strip()}")

            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            for child in Path(temp_root).iterdir():
                shutil.move(str(child), str(target_dir / child.name))

        latest_link = reshade_path / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            if latest_link.is_dir() and not latest_link.is_symlink():
                shutil.rmtree(latest_link)
            else:
                latest_link.unlink()
        latest_link.symlink_to(target_dir, target_is_directory=True)
        version_file = bin_dir / ("reshade_last_addon.version" if version == "last" else "reshade_latest_addon.version")
        version_label = version
        if version_file.exists():
            version_label = version_file.read_text(encoding="utf-8", errors="ignore").strip() or version
        (reshade_path / "LVERS").write_text(f"{version_label}{version_suffix}", encoding="utf-8")
        (target_dir / "addon_version").touch()

        self._install_optional_d3dcompiler(bin_dir, reshade_path)
        self._install_autohdr_payloads(main_path, bin_dir)
        self._install_hdr_shader_packs(main_path, bin_dir)
        self._install_specialk_runtime(main_path, bin_dir)
        self._write_default_reshade_ini(main_path)

    def _install_optional_d3dcompiler(self, bin_dir: Path, reshade_path: Path) -> None:
        compiler = bin_dir / "d3dcompiler_47.dll"
        if not compiler.exists():
            decky.logger.warning("d3dcompiler_47.dll not cached; relying on Proton/Wine")
            return
        for arch in ["32", "64"]:
            shutil.copy2(compiler, reshade_path / f"d3dcompiler_47.dll.{arch}")

    def _install_autohdr_payloads(self, main_path: Path, bin_dir: Path) -> None:
        addon_archive = bin_dir / "autohdr_addon.tar.gz"
        effect_archive = bin_dir / "advanced_autohdr_effect.tar.gz"
        addon_dir = main_path / "AutoHDR_addons"
        shader_dir = main_path / "ReShade_shaders" / "Merged" / "Shaders"
        texture_dir = main_path / "ReShade_shaders" / "Merged" / "Textures"

        if addon_archive.exists():
            with tarfile.open(addon_archive, "r:gz") as archive:
                archive.extractall(addon_dir)
            for addon in addon_dir.rglob("*"):
                lower = addon.name.lower()
                if addon.is_file() and ("64.addon" in lower or lower.endswith(".addon64")):
                    self._copy_if_different(addon, addon_dir / "AutoHDR64.addon")
                    self._copy_if_different(addon, addon_dir / "AutoHDR.addon64")
                if addon.is_file() and ("32.addon" in lower or lower.endswith(".addon32")):
                    self._copy_if_different(addon, addon_dir / "AutoHDR32.addon")
                    self._copy_if_different(addon, addon_dir / "AutoHDR.addon32")

        if effect_archive.exists():
            with tempfile.TemporaryDirectory(prefix=f"{PLUGIN_PACKAGE}-autohdr-") as temp_root:
                temp_path = Path(temp_root)
                with tarfile.open(effect_archive, "r:gz") as archive:
                    archive.extractall(temp_path)
                for path in temp_path.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() in [".fx", ".fxh"]:
                        shutil.copy2(path, shader_dir / path.name)
                    elif "texture" in str(path.parent).lower():
                        shutil.copy2(path, texture_dir / path.name)

        reshade_fxh = bin_dir / "ReShade.fxh"
        if reshade_fxh.exists():
            shutil.copy2(reshade_fxh, shader_dir / "ReShade.fxh")

    def _install_hdr_shader_packs(self, main_path: Path, bin_dir: Path) -> None:
        shader_dir = main_path / "ReShade_shaders" / "Merged" / "Shaders"
        texture_dir = main_path / "ReShade_shaders" / "Merged" / "Textures"
        shader_dir.mkdir(parents=True, exist_ok=True)
        texture_dir.mkdir(parents=True, exist_ok=True)

        lilium_archive = bin_dir / "lilium_hdr_shaders.7z"
        if lilium_archive.exists():
            self._extract_shader_archive(lilium_archive, shader_dir, texture_dir, preserve_lilium_layout=True)

        pumbo_archive = bin_dir / "PumboAutoHDR-master.zip"
        if pumbo_archive.exists():
            self._extract_shader_archive(pumbo_archive, shader_dir, texture_dir)

    def _extract_shader_archive(self, archive_path: Path, shader_dir: Path, texture_dir: Path, preserve_lilium_layout: bool = False) -> None:
        with tempfile.TemporaryDirectory(prefix=f"{PLUGIN_PACKAGE}-shader-pack-") as temp_root:
            temp_path = Path(temp_root)
            if archive_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(archive_path) as archive:
                    self._safe_extract(archive, temp_path)
            else:
                self._extract_with_7zip(archive_path, temp_path)

            for path in temp_path.rglob("*"):
                if not path.is_file():
                    continue
                lower_parent = str(path.parent).lower()
                suffix = path.suffix.lower()
                if suffix in [".fx", ".fxh"]:
                    relative = self._shader_pack_relative_path(path, temp_path)
                    if preserve_lilium_layout and relative:
                        target = shader_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
                    else:
                        shutil.copy2(path, shader_dir / path.name)
                elif suffix in [".png", ".jpg", ".jpeg", ".dds"] or "texture" in lower_parent:
                    shutil.copy2(path, texture_dir / path.name)

    def _shader_pack_relative_path(self, path: Path, root: Path) -> Path | None:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return None
        parts = list(relative.parts)
        lower_parts = [part.lower() for part in parts]
        for marker in ("shaders", "reshade-shaders", "reshade_shaders"):
            if marker in lower_parts:
                index = lower_parts.index(marker)
                if index + 1 < len(parts):
                    return Path(*parts[index + 1:])
        if len(parts) > 1:
            return Path(*parts[1:])
        return relative

    def _install_specialk_runtime(self, main_path: Path, bin_dir: Path) -> None:
        archive = bin_dir / "SpecialK.7z"
        specialk_dir = main_path / "SpecialK"
        if not archive.exists():
            return
        if specialk_dir.exists():
            shutil.rmtree(specialk_dir)
        specialk_dir.mkdir(parents=True, exist_ok=True)
        self._extract_with_7zip(archive, specialk_dir)

    def _extract_with_7zip(self, archive_path: Path, target_dir: Path) -> None:
        sevenzip = Path(self.bin_cache_path) / "7zz"
        if not sevenzip.exists():
            raise FileNotFoundError(f"Missing private 7-Zip extractor: {sevenzip}")
        result = subprocess.run(
            [str(sevenzip), "x", "-y", f"-o{target_dir}", str(archive_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not extract {archive_path.name}: {result.stderr.strip() or result.stdout.strip()}")

    def _copy_if_different(self, source: Path, target: Path) -> None:
        try:
            if source.resolve() == target.resolve():
                return
        except OSError:
            pass
        shutil.copy2(source, target)

    def _write_default_reshade_ini(self, main_path: Path) -> None:
        ini_path = main_path / "ReShade.ini"
        if ini_path.exists():
            self._ensure_reshade_tutorial_skipped(ini_path)
            self._ensure_game_relative_shader_paths(ini_path)
            return
        ini_path.write_text(
            "[GENERAL]\n"
            "EffectSearchPaths=.\\ReShade_shaders\\Merged\\Shaders\n"
            "TutorialProgress=4\n"
            "SkipLoadingDisabledEffects=1\n"
            "TextureSearchPaths=.\\ReShade_shaders\\Merged\\Textures\n"
            "PresetPath=.\\ReShadePreset.ini\n",
            encoding="utf-8",
        )
        try:
            ini_path.chmod(0o666)
        except OSError:
            pass

    def _ensure_reshade_tutorial_skipped(self, ini_path: Path) -> None:
        try:
            text = ini_path.read_text(encoding="utf-8", errors="ignore") if ini_path.exists() else ""
            if "[GENERAL]" not in text:
                text = "[GENERAL]\nTutorialProgress=4\n" + text
            elif re.search(r"(?im)^TutorialProgress\s*=", text):
                text = re.sub(r"(?im)^TutorialProgress\s*=.*$", "TutorialProgress=4", text)
            else:
                text = re.sub(r"(?im)^\[GENERAL\]\s*$", "[GENERAL]\nTutorialProgress=4", text, count=1)
            ini_path.write_text(text, encoding="utf-8")
            ini_path.chmod(0o666)
        except OSError as error:
            decky.logger.warning("Could not update ReShade tutorial state for %s: %s", ini_path, error)

    def _ensure_game_relative_shader_paths(self, ini_path: Path) -> None:
        try:
            text = ini_path.read_text(encoding="utf-8", errors="ignore") if ini_path.exists() else "[GENERAL]\n"
            replacements = {
                "EffectSearchPaths": ".\\ReShade_shaders\\Merged\\Shaders",
                "TextureSearchPaths": ".\\ReShade_shaders\\Merged\\Textures",
                "PresetPath": ".\\ReShadePreset.ini",
            }
            if "[GENERAL]" not in text:
                text = "[GENERAL]\n" + text
            for key, value in replacements.items():
                line = f"{key}={value}"
                if re.search(rf"(?im)^{re.escape(key)}\s*=", text):
                    text = re.sub(rf"(?im)^{re.escape(key)}\s*=.*$", lambda _match, line=line: line, text)
                else:
                    text = re.sub(r"(?im)^\[GENERAL\]\s*$", lambda _match, line=line: f"[GENERAL]\n{line}", text, count=1)
            ini_path.write_text(text, encoding="utf-8")
            ini_path.chmod(0o666)
        except OSError as error:
            decky.logger.warning("Could not update ReShade shader paths for %s: %s", ini_path, error)

    def _latest_reshade_addon_url(self) -> str:
        page = self._fetch_text("https://reshade.me/")
        if page:
            match = re.search(r'https://reshade\.me/downloads/ReShade_Setup_[^"\']+_Addon\.exe', page)
            if match:
                return match.group(0)
            match = re.search(r'downloads/(ReShade_Setup_[0-9.]+_Addon\.exe)', page)
            if match:
                return f"https://reshade.me/downloads/{match.group(1)}"

        # Stable fallback if the homepage changes format. RenoDX currently
        # needs add-on API 18, which ReShade 6.7.3 provides.
        return "https://reshade.me/downloads/ReShade_Setup_6.7.3_Addon.exe"

    def _reshade_installer_supports_renodx(self, installer: Path, version_file: Path | None = None) -> bool:
        version = (0, 0, 0)
        if version_file and version_file.exists():
            try:
                parts = [int(part) for part in version_file.read_text(encoding="utf-8").strip().split(".")[:3]]
                if len(parts) == 3:
                    version = tuple(parts)
            except (OSError, ValueError):
                version = (0, 0, 0)
        if version == (0, 0, 0):
            version = self._reshade_version_from_name(installer.name)
        return version >= RESHADE_MIN_RENODX_VERSION

    def _reshade_version_from_name(self, name: str) -> tuple[int, int, int]:
        match = re.search(r"ReShade_Setup_([0-9]+)\.([0-9]+)\.([0-9]+)", name, re.I)
        if not match:
            return (0, 0, 0)
        return tuple(int(part) for part in match.groups())

    def _download_latest_github_asset(self, api_url: str, target: Path, extensions: list[str]) -> None:
        release = self._fetch_json(api_url)
        if not isinstance(release, dict):
            raise ValueError(f"GitHub release lookup failed for {api_url}")
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            raise ValueError(f"GitHub release did not include assets for {api_url}")
        lowered_extensions = tuple(ext.lower() for ext in extensions)
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            url = asset.get("browser_download_url")
            if url and name.endswith(lowered_extensions):
                self._download_url(str(url), target)
                return
        raise FileNotFoundError(f"No matching release asset found for {api_url}")

    def _download_autohdr_payloads(self, bin_dir: Path, autohdr_archive: Path, advanced_archive: Path) -> None:
        source_zip = bin_dir / "AutoHDR-ReShade-main.zip"
        self._download_url("https://github.com/MajorPainTheCactus/AutoHDR-ReShade/archive/refs/heads/main.zip", source_zip)

        extract_dir = bin_dir / "AutoHDR-ReShade-main"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        with zipfile.ZipFile(source_zip) as archive:
            self._safe_extract(archive, extract_dir)

        files = [path for path in extract_dir.rglob("*") if path.is_file()]
        addon_files = [path for path in files if path.name.lower().endswith((".addon", ".addon32", ".addon64")) or ".addon" in path.name.lower()]
        shader_files = [path for path in files if path.suffix.lower() in [".fx", ".fxh"]]

        if not addon_files:
            raise FileNotFoundError("AutoHDR add-on files were not found in downloaded archive")
        if not shader_files:
            raise FileNotFoundError("AutoHDR shader files were not found in downloaded archive")

        with tarfile.open(autohdr_archive, "w:gz") as tar:
            for path in addon_files:
                tar.add(path, arcname=path.name)

        with tarfile.open(advanced_archive, "w:gz") as tar:
            for path in shader_files:
                tar.add(path, arcname=f"Shaders/{path.name}")

    def _download_7zip_binary(self, bin_dir: Path) -> None:
        machine = platform.machine().lower()
        archive_name = "7z2501-linux-arm64.tar.xz" if machine in ["aarch64", "arm64"] else "7z2501-linux-x64.tar.xz"
        archive_path = bin_dir / archive_name
        self._download_url(f"https://www.7-zip.org/a/{archive_name}", archive_path)

        extract_dir = bin_dir / "7zip"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:xz") as archive:
            archive.extractall(extract_dir)

        candidate = extract_dir / "7zz"
        if not candidate.exists():
            matches = list(extract_dir.rglob("7zz"))
            if not matches:
                raise FileNotFoundError("7zz was not found in downloaded 7-Zip archive")
            candidate = matches[0]

        target = bin_dir / "7zz"
        shutil.copy2(candidate, target)
        target.chmod(0o755)

    def _ssl_context_candidates(self) -> list[tuple[Any, str]]:
        """TLS contexts to try in order: verified first, unverified last resort.

        Some SteamOS/Decky sandboxes ship without a usable CA bundle; the
        unverified fallback keeps downloads working there, but is only used
        after verified TLS fails and its use is logged.
        """
        if not SSL_AVAILABLE:
            return []
        candidates: list[tuple[Any, str]] = []
        try:
            candidates.append((ssl.create_default_context(), "verified"))
        except Exception:
            pass
        insecure = ssl.create_default_context()
        insecure.check_hostname = False
        insecure.verify_mode = ssl.CERT_NONE
        candidates.append((insecure, "unverified"))
        return candidates

    def _fetch_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 DeckyRenoDX/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        for context, label in self._ssl_context_candidates() or [(None, "no-ssl-module")]:
            try:
                kwargs: dict[str, Any] = {"timeout": 15}
                if context is not None:
                    kwargs["context"] = context
                with urllib.request.urlopen(request, **kwargs) as response:
                    if label == "unverified":
                        decky.logger.warning("Fetched %s without TLS verification (no usable CA store).", url)
                    return response.read().decode("utf-8", "ignore")
            except Exception as error:
                decky.logger.warning("Python text fetch failed for %s (%s TLS): %s", url, label, error)
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "-A", "Mozilla/5.0 DeckyRenoDX/1.0", url],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env=self._clean_subprocess_env(),
            )
            if result.returncode == 0:
                return result.stdout
            decky.logger.warning("curl text fetch failed for %s: %s", url, result.stderr.strip()[-160:])
        except (OSError, subprocess.TimeoutExpired) as error:
            decky.logger.warning("curl text fetch errored for %s: %s", url, error)
            return ""

    def _download_url(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 DeckyRenoDX/1.0"})
        self._download_file(request, target)

    async def check_renodx_support(self, game_name: str, engine: str = "") -> dict[str, Any]:
        try:
            # The mod list may hit the network; keep it off the event loop.
            mods = await asyncio.to_thread(self._renodx_mod_list)
            match = self._match_renodx_mod(game_name, mods, engine)
            if match is None:
                return {
                    "status": "success",
                    "supported": False,
                    "query": game_name,
                    "message": "No RenoDX mod was found for this title.",
                }
            return {
                "status": "success",
                "supported": True,
                "query": game_name,
                "match": match,
                "message": f"RenoDX mod found: {match['name']}",
            }
        except Exception as error:
            decky.logger.exception("RenoDX support check failed")
            return {"status": "error", "supported": False, "query": game_name, "message": str(error)}

    def _renodx_mod_list(self) -> list[dict[str, Any]]:
        cache_file = Path(self.main_path) / "renodx_mods_cache.json"
        cached = self._read_renodx_mod_cache(cache_file)
        if cached and time.time() - float(cached.get("fetched_at", 0)) < 86400:
            return list(cached.get("mods", []))

        request = urllib.request.Request(RENODX_MODS_URL, headers={"User-Agent": PLUGIN_PACKAGE})
        temp_file = Path(tempfile.gettempdir()) / f"{PLUGIN_PACKAGE}-mods.md"
        self._download_file(request, temp_file)
        mods = self._parse_renodx_mods(temp_file.read_text(encoding="utf-8", errors="ignore"))
        if not mods:
            raise ValueError("RenoDX mods list was empty or could not be parsed.")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "mods": mods}, indent=2), encoding="utf-8")
        self._fix_deck_user_ownership(cache_file.parent)
        return mods

    def _read_renodx_mod_cache(self, cache_file: Path) -> dict[str, Any] | None:
        try:
            if cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                mods = data.get("mods", []) if isinstance(data, dict) else []
                if mods and not all(("match_type" in mod and "addon_url" in mod) for mod in mods[:20] if isinstance(mod, dict)):
                    cache_file.unlink(missing_ok=True)
                    return None
                return data
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _parse_renodx_mods(self, markdown: str) -> list[dict[str, Any]]:
        mods = []
        section = ""
        table: list[str] = []
        generic_engine = ""
        generic_links: list[str] = []

        def flush_table() -> None:
            nonlocal table
            if not table:
                return
            if section == "specific":
                mods.extend(self._parse_renodx_specific_table(table))
            elif section == "multi" and generic_engine:
                mods.extend(self._parse_renodx_multi_game_table(table, generic_engine, generic_links))
            table = []

        for raw_line in markdown.splitlines():
            line = raw_line.rstrip()
            heading = re.match(r"^\s*(#{1,4})\s+(.+?)\s*$", line)
            if heading:
                flush_table()
                title = self._strip_markdown(heading.group(2)).lower()
                heading_links = re.findall(r"https?://[^\s)]+", line)
                if title == "list":
                    section = "specific"
                    generic_engine = ""
                    generic_links = []
                elif title == "multi-game mods":
                    section = "multi"
                    generic_engine = ""
                    generic_links = []
                elif section == "multi" and "engine" in title:
                    generic_engine = self._renodx_engine_bucket(title)
                    generic_links = heading_links
                elif title in {"related mods", "deprecated mods"}:
                    section = ""
                    generic_engine = ""
                    generic_links = []
                continue
            if line.lstrip().startswith("|"):
                table.append(line)
            elif table:
                flush_table()
            elif section == "multi" and generic_engine and "http" in line:
                generic_links.extend(re.findall(r"https?://[^\s)]+", line))
        flush_table()
        if not mods:
            mods.extend(self._parse_renodx_legacy_rows(markdown))
        return mods

    def _parse_renodx_legacy_rows(self, markdown: str) -> list[dict[str, Any]]:
        mods: list[dict[str, Any]] = []
        row_pattern = re.compile(
            r"\|\s*(?P<name>[^|\n]+?)\s*\|\s*(?P<maintainer>[^|\n]*?)\s*\|\s*(?P<links>.*?)\s*\|\s*(?P<status>(?:\[:(?:white_check_mark|construction):\]\(#\s*\"[^\"]*\"\)|:white_check_mark:|:construction:|[^|\n]*?))\s*\|",
            re.I | re.S,
        )
        for match in row_pattern.finditer(markdown):
            name = self._strip_markdown(match.group("name"))
            if not self._valid_renodx_name(name):
                continue
            links = self._renodx_links(match.group("links"))
            mods.append({
                "name": name,
                "normalized": self._normalize_game_title(name),
                "maintainer": self._strip_markdown(match.group("maintainer")),
                "status": self._renodx_status_label(match.group("status")),
                "notes": self._renodx_notes(match.group("status")),
                **links,
                "source_type": self._renodx_source_type(links["links"]),
                "bitness": self._renodx_bitness(links["links"]),
                "engine_bucket": "",
                "match_type": "specific",
            })
        return mods

    def _parse_renodx_specific_table(self, rows: list[str]) -> list[dict[str, Any]]:
        mods: list[dict[str, Any]] = []
        for cells in self._markdown_table_rows(rows):
            if len(cells) < 4:
                continue
            name_cell, maintainer, links_cell, status_cell = cells[:4]
            name = self._strip_markdown(name_cell)
            if not self._valid_renodx_name(name):
                continue
            links = self._renodx_links(links_cell)
            mods.append({
                "name": name,
                "normalized": self._normalize_game_title(name),
                "maintainer": self._strip_markdown(maintainer),
                "status": self._renodx_status_label(status_cell),
                "notes": self._renodx_notes(status_cell),
                **links,
                "source_type": self._renodx_source_type(links["links"]),
                "bitness": self._renodx_bitness(links["links"]),
                "engine_bucket": "",
                "match_type": "specific",
            })
        return mods

    def _parse_renodx_multi_game_table(self, rows: list[str], engine_bucket: str, addon_links: list[str]) -> list[dict[str, Any]]:
        mods: list[dict[str, Any]] = []
        shared_links = self._renodx_links(" ".join(addon_links))
        for cells in self._markdown_table_rows(rows):
            if len(cells) < 2:
                continue
            name = self._strip_markdown(cells[0])
            if not self._valid_renodx_name(name):
                continue
            notes_cell = cells[2] if len(cells) > 2 else ""
            mods.append({
                "name": name,
                "normalized": self._normalize_game_title(name),
                "maintainer": "RenoDX",
                "status": self._renodx_status_label(cells[1]),
                "notes": [self._strip_markdown(notes_cell)] if self._strip_markdown(notes_cell) else [],
                **shared_links,
                "source_type": "generic",
                "bitness": self._renodx_bitness(shared_links["links"]),
                "engine_bucket": engine_bucket,
                "match_type": "generic_listed",
            })
        if engine_bucket and shared_links["addon_url"]:
            mods.append({
                "name": f"Generic {engine_bucket.title()} Engine",
                "normalized": f"generic{engine_bucket}engine",
                "maintainer": "RenoDX",
                "status": "experimental",
                "notes": ["Experimental generic engine install. Use only when no exact game entry exists."],
                **shared_links,
                "source_type": "generic",
                "bitness": self._renodx_bitness(shared_links["links"]),
                "engine_bucket": engine_bucket,
                "match_type": "generic_engine",
            })
        return mods

    def _markdown_table_rows(self, rows: list[str]) -> list[list[str]]:
        parsed = []
        for row in rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if not cells or all(set(cell) <= {":", "-"} for cell in cells if cell):
                continue
            if cells[0].lower() in {"name", "game"}:
                continue
            parsed.append(cells)
        return parsed

    def _valid_renodx_name(self, name: str) -> bool:
        return bool(name and name.lower() not in {"name", "game"} and not set(name) <= {":", "-"})

    def _renodx_links(self, value: str) -> dict[str, Any]:
        links = [url.rstrip(".,") for url in re.findall(r"https?://[^\s)]+", value or "")]
        addon_links = [url for url in links if re.search(r"\.addon(?:32|64)?(?:$|[?#])", url.lower())]
        snapshot_links = [
            url.rstrip(".,")
            for _badge, url in re.findall(r"\[!\[([^\]]+)\]\([^)]+\)\]\((https?://[^)]+)\)", value or "", re.I)
            if "snapshot" in _badge.lower() and re.search(r"\.addon(?:32|64)?(?:$|[?#])", url.lower())
        ]
        if snapshot_links:
            addon_links = snapshot_links + [url for url in addon_links if url not in snapshot_links]
        page_links = [url for url in links if url not in addon_links]
        return {
            "links": links,
            "addon_url": addon_links[0] if addon_links else "",
            "snapshotLinks": snapshot_links or addon_links,
            "pageLinks": page_links,
            "manual_url": page_links[0] if page_links and not addon_links else "",
        }

    def _renodx_source_type(self, links: list[str]) -> str:
        if not links:
            return "unknown"
        lower = " ".join(links).lower()
        if "github.com" in lower and "releases/download" in lower:
            return "github_release"
        if "github.io" in lower or ".addon" in lower:
            return "snapshot"
        if "nexusmods.com" in lower:
            return "nexus"
        if "discord." in lower:
            return "discord"
        return "page"

    def _renodx_bitness(self, links: list[str]) -> str:
        text = " ".join(links).lower()
        has64 = ".addon64" in text or "64.addon" in text
        has32 = ".addon32" in text or "32.addon" in text
        if has64 and has32:
            return "both"
        if has64:
            return "64"
        if has32:
            return "32"
        return "unknown"

    def _renodx_notes(self, status_cell: str) -> list[str]:
        notes = []
        for note in re.findall(r'\]\(#\s*"([^"]+)"\)', status_cell or ""):
            clean = self._strip_markdown(note)
            if clean:
                notes.append(clean)
        return notes

    def _renodx_engine_bucket(self, heading: str) -> str:
        lower = heading.lower()
        if "unreal" in lower:
            return "unreal"
        if "unity" in lower:
            return "unity"
        return self._normalize_game_title(heading.replace("engine", ""))

    def _strip_markdown(self, value: str) -> str:
        value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
        value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
        value = re.sub(r":(?:white_check_mark|construction):", "", value)
        value = re.sub(r"<[^>]+>", "", value)
        return re.sub(r"\s+", " ", value).strip()

    def _renodx_status_label(self, status_cell: str) -> str:
        lower = status_cell.lower()
        if "white_check_mark" in lower:
            return "working"
        if "construction" in lower:
            return "in_progress"
        return self._strip_markdown(status_cell) or "listed"

    def _normalize_game_title(self, title: str) -> str:
        # Transliterate diacritics (ö -> o, é -> e) so "Ragnarök" matches "Ragnarok".
        title = unicodedata.normalize("NFKD", title)
        title = "".join(ch for ch in title if not unicodedata.combining(ch))
        title = title.lower()
        title = title.replace("™", "").replace("®", "")
        title = re.sub(r"\([^)]*\)", " ", title)
        title = re.sub(r"\b(the|definitive edition|directors cut|director's cut|remastered|remake|dx10|dx11|dx12|steam only)\b", " ", title)
        return re.sub(r"[^a-z0-9]+", "", title)

    _SEQUEL_SUFFIX_RE = re.compile(r"^(?:i{1,3}|iv|v|vi{1,3}|ix|x{1,3}|xi{1,3}|xiv|xv|[0-9]{1,2})$")

    def _is_sequel_suffix(self, suffix: str) -> bool:
        # "2", "iii", or anything starting with a digit ("2remaster") signals a
        # different entry in the series, not an edition of the same game.
        return bool(suffix) and (suffix[0].isdigit() or bool(self._SEQUEL_SUFFIX_RE.match(suffix)))

    def _match_renodx_mod(self, game_name: str, mods: list[dict[str, Any]], engine: str = "") -> dict[str, Any] | None:
        query = self._normalize_game_title(game_name)
        if not query:
            return None

        # RenoDX Commander parity: exact normalized match first, then containment
        # ("Code Vein GOTY" matches "Code Vein") with sequel-suffix rejection so
        # "Final Fantasy XIII" never matches "Final Fantasy X".
        best: tuple[int, int, dict[str, Any]] | None = None
        for mod in mods:
            if mod.get("match_type") == "generic_engine":
                continue
            candidate = str(mod.get("normalized", ""))
            if not candidate:
                continue
            score = 0
            if candidate == query:
                score = 100
            elif len(candidate) >= 4 and query.startswith(candidate):
                if not self._is_sequel_suffix(query[len(candidate):]):
                    score = 85
            elif len(query) >= 4 and candidate.startswith(query):
                if not self._is_sequel_suffix(candidate[len(query):]):
                    score = 80
            elif len(candidate) >= 6 and candidate in query:
                score = 75
            if score >= 70 and (best is None or (score, len(candidate)) > (best[0], best[1])):
                best = (score, len(candidate), mod)

        if best is not None:
            result = dict(best[2])
            result["score"] = best[0]
            return result

        engine_bucket = self._renodx_engine_bucket(engine or "")
        generic = self._generic_renodx_mod_for_engine(mods, engine_bucket)
        if generic:
            return generic
        return None

    def _generic_renodx_mod_for_engine(self, mods: list[dict[str, Any]], engine: str) -> dict[str, Any] | None:
        engine_bucket = self._renodx_engine_bucket(engine or "")
        if engine_bucket in {"unreal", "unity"}:
            for mod in mods:
                if mod.get("match_type") == "generic_engine" and mod.get("engine_bucket") == engine_bucket:
                    result = dict(mod)
                    result["score"] = 62
                    result["experimental"] = True
                    return result
        return None

    def _fallback_generic_renodx_mod(self, engine: str) -> dict[str, Any]:
        engine_bucket = self._renodx_engine_bucket(engine or "")
        urls = {
            "unreal": "https://clshortfuse.github.io/renodx/renodx-unrealengine.addon64",
            "unity": "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon64",
        }
        addon_url = urls.get(engine_bucket, "")
        if not addon_url:
            return {}
        return {
            "name": f"Generic {engine_bucket.title()} Engine",
            "normalized": f"generic{engine_bucket}engine",
            "maintainer": "RenoDX",
            "status": "experimental",
            "notes": ["Experimental generic engine install. Use only when no exact game entry exists."],
            "links": [addon_url],
            "addon_url": addon_url,
            "snapshotLinks": [addon_url],
            "pageLinks": [],
            "manual_url": "",
            "source_type": "generic_fallback",
            "bitness": "64",
            "engine_bucket": engine_bucket,
            "match_type": "generic_engine",
            "score": 62,
            "experimental": True,
        }

    async def _resolve_renodx_match_for_install(self, appid: str, title: str, exe_path: str, context: dict[str, Any], logger=None) -> dict[str, Any]:
        mod = context.get("renodx_match") or {}
        if mod:
            return mod

        engine = str(context.get("engine") or "")
        if not engine and exe_path and os.path.exists(exe_path):
            try:
                api_info = await self._detect_api_with_cache(exe_path, logger)
                engine = str(api_info.get("engine") or "")
            except Exception:
                engine = ""
        if not engine:
            try:
                wiki_data = await asyncio.to_thread(self.wiki_scraper.get_game_data, appid)
                engine = str(wiki_data.get("engine") or "")
            except Exception:
                engine = ""

        architecture = str(context.get("architecture") or "")
        if not architecture and exe_path and os.path.exists(exe_path):
            try:
                api_info = await self._detect_api_with_cache(exe_path, logger)
                architecture = str(api_info.get("architecture") or "")
            except Exception:
                architecture = ""

        result = await self.check_renodx_support(title, engine)
        mod = result.get("match", {}) if result.get("supported") else {}
        if mod:
            if mod.get("match_type") == "generic_engine" and architecture != "64":
                if logger:
                    logger.info("Skipping generic RenoDX %s because architecture is %s, not confirmed 64-bit.", mod.get("name"), architecture or "unknown")
                return {}
            return mod

        generic = self._generic_renodx_mod_for_engine(await asyncio.to_thread(self._renodx_mod_list), engine)
        if generic:
            if architecture != "64":
                if logger:
                    logger.info("Skipping generic RenoDX %s because architecture is %s, not confirmed 64-bit.", generic.get("name"), architecture or "unknown")
                return {}
            return generic
        if architecture != "64":
            if logger:
                logger.info("Skipping built-in generic RenoDX fallback because architecture is %s, not confirmed 64-bit.", architecture or "unknown")
            return {}
        fallback = self._fallback_generic_renodx_mod(engine)
        if fallback and logger:
            logger.info("Using built-in generic RenoDX fallback for %s engine: %s", fallback.get("engine_bucket"), fallback.get("addon_url"))
        return fallback or {}

    async def manage_game_reshade(self, appid: str, action: str, dll_override: str = "dxgi", vulkan_mode: str = "", selected_executable_path: str = "") -> dict:
        try:
            logger = setup_per_game_logger(appid)
            if action == "install":
                runtime_result = await asyncio.to_thread(self._ensure_latest_reshade_runtime_sync)
                if runtime_result.get("status") != "success":
                    return runtime_result
            if action in {"install", "uninstall", "remove"}:
                compat_result = self._clear_steam_compatdata(appid, logger)
                if compat_result["status"] == "error":
                    return {"status": "error", "message": compat_result["message"], "compatdata": compat_result}
            assets_dir = self._get_assets_dir()
            script_path = assets_dir / "reshade-game-manager.sh"
            
            # Track if user selected a specific executable path
            using_user_selected_path = bool(selected_executable_path and os.path.exists(selected_executable_path))
            
            try:
                # Use selected executable path if provided, otherwise use detection
                if using_user_selected_path:
                    game_path = os.path.dirname(selected_executable_path)
                    decky.logger.info(f"Using user-selected executable path: {selected_executable_path}")
                    decky.logger.info(f"Installing ReShade to directory: {game_path}")
                elif action == "install":
                    # Get the base game installation path (not executable-specific directory)
                    game_path = self._find_game_path(appid)
                    decky.logger.info(f"Using base game path for Bash detection: {game_path}")
                else:
                    # For uninstall, we still need to find where ReShade was installed
                    # Try to use our detection first, then fall back to base path
                    try:
                        exe_result = await self.find_game_executable_path(appid)
                        if (exe_result["status"] == "success" and 
                            exe_result.get("steam_logs_result", {}).get("status") == "success"):
                            game_path = os.path.dirname(exe_result["steam_logs_result"]["executable_path"])
                            decky.logger.info(f"Using detected executable directory for uninstall: {game_path}")
                        elif (exe_result["status"] == "success" and 
                              exe_result.get("enhanced_detection_result", {}).get("status") == "success"):
                            game_path = os.path.dirname(exe_result["enhanced_detection_result"]["executable_path"])
                            decky.logger.info(f"Using enhanced detection directory for uninstall: {game_path}")
                        else:
                            game_path = self._find_game_path(appid)
                            decky.logger.info(f"Using base game path for uninstall: {game_path}")
                    except:
                        game_path = self._find_game_path(appid)
                        decky.logger.info(f"Using base game path for uninstall (fallback): {game_path}")
                
                decky.logger.info(f"Final game path: {game_path}")
            except ValueError as e:
                return {"status": "error", "message": str(e)}

            # Build command - if user selected a specific path, don't pass appid to prevent bash script from overriding
            cmd = ["/bin/bash", str(script_path), action, game_path, dll_override]
            if vulkan_mode:
                cmd.extend([vulkan_mode, self._deck_expanduser(f"~/.local/share/Steam/steamapps/compatdata/{appid}"), appid])
            else:
                # For non-Vulkan mode, add empty placeholders for vulkan_mode and wineprefix
                if using_user_selected_path:
                    # Don't pass appid when using user-selected path to prevent bash script from overriding
                    cmd.extend(["", "", ""])
                    decky.logger.info("Not passing App ID to bash script to prevent path override")
                else:
                    # Pass appid for automatic detection
                    cmd.extend(["", "", appid])
            
            decky.logger.info(f"Executing command: {' '.join(cmd)}")
            
            # Create environment with required LD_LIBRARY_PATH fix for Decky v3.1.10+
            clean_env = {**os.environ, **self.environment}
            clean_env["LD_LIBRARY_PATH"] = ""
            
            # The install script can run for minutes; keep it off the event loop
            # so other plugin calls (status polls, UI refreshes) stay responsive.
            process = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=str(assets_dir),
                env=clean_env,
                capture_output=True,
                text=True,
            )
            
            decky.logger.info(f"Script output: {process.stdout}")
            if process.stderr:
                decky.logger.error(f"Script errors: {process.stderr}")
            
            if process.returncode != 0:
                return {"status": "error", "message": process.stderr}
            install_dir = Path(game_path)
            if selected_executable_path and os.path.exists(selected_executable_path):
                install_dir = Path(selected_executable_path).parent
            else:
                try:
                    install_dir = self._resolve_game_exe_dir(appid, selected_executable_path)
                except Exception:
                    install_dir = Path(game_path)
            if action == "uninstall":
                self._remove_game_hdr_marker(Path(game_path))
            elif action == "install":
                verify_success, verify_message = self.installer.verify_reshade(str(install_dir))
                if not verify_success:
                    logger.error("ReShade install verification failed: %s", verify_message)
                    return {
                        "status": "error",
                        "message": verify_message,
                        "output": process.stdout,
                    }
                effective_dll = self._parse_reshade_selected_api(process.stdout) or dll_override
                if effective_dll == "auto":
                    effective_dll = "dxgi"
                installed_files = []
                for name in [
                    f"{effective_dll}.dll",
                    "d3dcompiler_47.dll",
                    "ReShade.ini",
                    "ReShadePreset.ini",
                    "ReShade.log",
                    "ReShade_README.txt",
                    "ReShade_shaders",
                    "AutoHDR32.addon",
                    "AutoHDR64.addon",
                    "AutoHDR.addon32",
                    "AutoHDR.addon64",
                    ".decky-renodx-hdr.json",
                ]:
                    path = install_dir / name
                    if path.exists() or path.is_symlink():
                        installed_files.append(str(path))
                arch = "64"
                try:
                    detected = await self._detect_api_for_path(str(install_dir))
                    if detected.get("status") == "success":
                        arch = str(detected.get("architecture") or "64")
                except Exception:
                    pass
                wine_override = {"modified_files": [], "backups": {}, "wine_dll_overrides": {}}
                if effective_dll == "opengl32":
                    wine_override = self._set_wine_dll_override(appid, "opengl32", "native,builtin", logger)
                self._write_game_hdr_marker(
                    install_dir,
                    appid,
                    "reshade-hdr",
                    effective_dll,
                    arch,
                    {
                        "wine_dll_overrides": wine_override.get("wine_dll_overrides", {}),
                    },
                )
                marker = install_dir / ".decky-renodx-hdr.json"
                if str(marker) not in installed_files:
                    installed_files.append(str(marker))
                self.manifest_manager.write_manifest(appid, {
                    "appid": appid,
                    "method": "reshade",
                    "installed_files": installed_files,
                    "modified_files": wine_override.get("modified_files", []),
                    "backups": wine_override.get("backups", {}),
                    "launch_options_after": self._hdr_launch_options(effective_dll, appid),
                    "wine_dll_overrides": wine_override.get("wine_dll_overrides", {}),
                    "verified": verify_success,
                    "verification_notes": verify_message,
                    "plugin_version": self._current_version(),
                })
            self._fix_deck_user_ownership(game_path)
            response = {"status": "success", "output": process.stdout}
            if action == "install":
                effective_dll = self._parse_reshade_selected_api(process.stdout) or dll_override
                if effective_dll == "auto":
                    effective_dll = "dxgi"
                response["injection_dll"] = effective_dll
                response["launch_options"] = self._hdr_launch_options(effective_dll, appid)
                await self._set_steam_launch_options(appid, response["launch_options"])
            return response
        except Exception as e:
            decky.logger.error(str(e))
            return {"status": "error", "message": str(e)}

    def _ensure_latest_reshade_runtime_sync(self) -> dict[str, Any]:
        try:
            asset_result = self._ensure_runtime_assets()
            if not asset_result["ok"]:
                return {"status": "error", "message": asset_result["message"]}
            version_file = Path(self.bin_cache_path) / "reshade_latest_addon.version"
            wanted_version = version_file.read_text(encoding="utf-8", errors="ignore").strip() if version_file.exists() else ""
            lvers = Path(self.main_path) / "reshade" / "LVERS"
            installed_version = lvers.read_text(encoding="utf-8", errors="ignore").strip() if lvers.exists() else ""
            latest_dir = Path(self.main_path) / "reshade" / "latest"
            latest64 = latest_dir / "ReShade64.dll"
            if wanted_version and wanted_version not in installed_version:
                self._install_hdr_runtime_python("latest")
            elif not latest64.exists() or latest64.stat().st_size < 1024 * 1024:
                self._install_hdr_runtime_python("latest")
            return {"status": "success"}
        except Exception as error:
            decky.logger.exception("Failed to refresh latest ReShade runtime")
            return {"status": "error", "message": f"Could not refresh latest ReShade runtime: {error}"}

    async def install_hdr_fallback(self, appid: str, dll_override: str = "auto", selected_executable_path: str = "") -> dict:
        """Install the best automatic non-RenoDX HDR fallback for a Steam game."""
        try:
            logger = setup_per_game_logger(appid)
            compat_result = self._clear_steam_compatdata(appid, logger)
            if compat_result["status"] == "error":
                return {"status": "error", "message": compat_result["message"], "compatdata": compat_result}
            reshade_check = await self.check_reshade_path()
            if not reshade_check.get("exists"):
                return {"status": "error", "message": "Install the HDR runtime first."}

            exe_dir = self._resolve_game_exe_dir(appid, selected_executable_path)
            api = dll_override
            arch = "64"
            if dll_override == "auto":
                detected = await self._detect_api_for_path(str(exe_dir))
                if detected.get("status") == "success":
                    api = str(detected.get("api") or "dxgi")
                    arch = str(detected.get("architecture") or "64")
                else:
                    api = "dxgi"
            else:
                detected = await self._detect_api_for_path(str(exe_dir))
                if detected.get("status") == "success":
                    arch = str(detected.get("architecture") or "64")

            exe_dir = self._compat_specialk_install_dir(appid, exe_dir)
            api = self._specialk_dll_for_game(appid, {"injection_dll": api}, api)
            specialk_result = self._install_specialk_for_game(exe_dir, api, arch, appid)
            if specialk_result.get("status") == "success":
                launch_options = self._hdr_launch_options(str(specialk_result["dll"]), appid, "special_k")
                await self._set_steam_launch_options(appid, launch_options)
                self._write_game_hdr_marker(exe_dir, appid, "specialk", str(specialk_result["dll"]), arch)
                self._fix_deck_user_ownership(exe_dir)
                return {
                    "status": "success",
                    "method": "specialk",
                    "launch_options": launch_options,
                    "output": (
                        f"Installed Special K HDR fallback for {api.upper()} ({arch}-bit).\n"
                        "Use Special K's in-game overlay to enable or tune HDR if it does not engage automatically.\n"
                        f"Use this launch option: {launch_options}"
                    ),
                }

            decky.logger.warning("Special K fallback was not applied: %s", specialk_result.get("message"))
            reshade_result = await self.manage_game_reshade(appid, "install", api, "", selected_executable_path)
            if reshade_result.get("status") == "success":
                if reshade_result.get("launch_options"):
                    await self._set_steam_launch_options(appid, str(reshade_result["launch_options"]))
                self._write_game_hdr_marker(exe_dir, appid, "reshade-hdr", api, arch)
                reshade_result["method"] = "reshade-hdr"
                reshade_result["output"] = (
                    f"Special K was not available for this game, so ReShade HDR shaders were installed instead.\n"
                    f"Included HDR shaders: AutoHDR, Pumbo AdvancedAutoHDR, and Lilium HDR shaders.\n"
                    f"{reshade_result.get('output', '')}"
                )
            return reshade_result
        except Exception as e:
            decky.logger.exception("HDR fallback install failed")
            return {"status": "error", "message": str(e)}

    async def get_hdr_recommendation(self, appid: str, title: str, game_path: str = "") -> dict:
        """Fetch recommendations for a game based on the decision tree."""
        try:
            # 1. Setup per-game logger
            logger = setup_per_game_logger(appid)
            logger.info(f"Fetching recommendation for {title} (AppID: {appid})")

            # 2. Get game context
            context = {
                "appid": appid,
                "title": title,
                "graphics_api": "unknown",
                "architecture": "unknown",
                "anti_cheat": [],
                "is_multiplayer": False,
                "native_hdr": "unknown",
                "special_k_wiki": False,
                "renodx_supported": False,
                "luma_supported": False,
                "special_k_verified": False,
                "special_k_wrapper": False,
                "injection_dll": "auto",
                "engine": "unknown",
                "renodx_flow_enabled": False,
                "special_k_notes": [],
                "special_k_delay_seconds": "0",
                "has_renodx_compat": False,
                "has_special_k_compat": False,
                "method_options": [],
            }

            if hasattr(self, "compat_db") and str(appid) in self.compat_db.get("games", {}):
                game_compat = self.compat_db["games"][str(appid)]
                tools = game_compat.get("tools", {})
                context["has_renodx_compat"] = "renodx" in tools
                context["has_special_k_compat"] = "special_k" in tools
                if "special_k" in tools:
                    special_k_tool = tools["special_k"]
                    context["tools"] = context.get("tools", {})
                    context["tools"]["special_k"] = self._compat_tool_metadata("special_k", special_k_tool)
                    context["special_k_delay_seconds"] = str(special_k_tool.get("special_k_delay_seconds", 0))
                    context["special_k_avoid_hdr"] = self._compat_specialk_avoid_hdr(appid)
                    forced_api = self._compat_specialk_force_render_api(appid)
                    if forced_api:
                        context["graphics_api"] = forced_api
                        context["injection_dll"] = self._api_to_injection_dll(forced_api)
                        context["api_source"] = "compatibility_json"
                    special_k_dll = self._compat_specialk_hook_dll(appid)
                    if special_k_dll:
                        context["special_k_injection_dll"] = special_k_dll
                        context["injection_dll"] = special_k_dll

            resolved_game_path = game_path
            if not resolved_game_path or not os.path.exists(resolved_game_path):
                resolved_game_path = await self._resolve_game_executable_for_recommendation(appid, logger)
            special_k_override = self.persistent_cache.get(f"specialk_verified_{appid}", expiry_days=3650)
            if isinstance(special_k_override, bool):
                context["special_k_verified"] = special_k_override

            # Fetch PCGamingWiki data. The scraper may cache remote responses, but
            # local game API/state is intentionally recomputed every refresh.
            wiki_data = await asyncio.to_thread(self.wiki_scraper.get_game_data, appid)
            if "status" not in wiki_data:
                context["native_hdr"] = wiki_data.get("native_hdr", "unknown")
                context["pcgw_page_name"] = wiki_data.get("page_name", "")
                if wiki_data.get("graphics_api") and wiki_data.get("graphics_api") != "unknown":
                    context["graphics_api"] = wiki_data.get("graphics_api", "unknown")
                    context["injection_dll"] = self._api_to_injection_dll(context["graphics_api"])
                    context["api_source"] = wiki_data.get("api_source", "pcgamingwiki_api_table")
                    context["api_page"] = wiki_data.get("api_page", "")
                if wiki_data.get("engine"):
                    context["engine"] = wiki_data.get("engine")
                context["special_k_wiki"] = wiki_data.get("special_k_compatible", False)
                context["special_k_notes"] = wiki_data.get("special_k_notes", [])
                context["special_k_delay_seconds"] = wiki_data.get("special_k_delay_seconds", "0")
                logger.info(f"Wiki data fetched: Native HDR={context['native_hdr']}, SK Compatible={context['special_k_wiki']}, Engine={context.get('engine', 'unknown')}")

            await self._refresh_renodx_recommendation_context(context, title, logger)

            # Detect API and anti-cheat from current disk state on every refresh.
            if resolved_game_path and os.path.exists(resolved_game_path):
                api_info = await self._detect_api_with_cache(resolved_game_path, logger)
                context["graphics_api"] = api_info.get("api", "unknown")
                context["architecture"] = api_info.get("architecture", "unknown")
                context["injection_dll"] = api_info.get("injection_dll", "auto")
                forced_api = self._compat_specialk_force_render_api(appid)
                if forced_api:
                    context["graphics_api"] = forced_api
                    context["injection_dll"] = self._api_to_injection_dll(forced_api)
                    context["api_source"] = "compatibility_json"
                special_k_dll = self._compat_specialk_hook_dll(appid)
                if special_k_dll:
                    context["special_k_injection_dll"] = special_k_dll
                    context["injection_dll"] = special_k_dll
                
                # Only overwrite engine if disk scan actually found one
                disk_engine = api_info.get("engine", "unknown")
                if disk_engine != "unknown":
                    context["engine"] = disk_engine
                    
                await self._refresh_renodx_recommendation_context(context, title, logger)

                context["anti_cheat"] = self.ac_detector.detect(str(Path(resolved_game_path).parent))
            if context["graphics_api"] == "unknown":
                await self._apply_pcgw_api_fallback(context, appid, logger)
                await self._refresh_renodx_recommendation_context(context, title, logger)
            if context["graphics_api"] == "unknown":
                await self._apply_steam_metadata_api_fallback(context, appid, logger)
                await self._refresh_renodx_recommendation_context(context, title, logger)

            # 3. Evaluate recommendations
            recommendations = self.decision_tree.evaluate(context)
            context["method_options"] = self._hdr_method_options(appid, context, recommendations)
            
            for rec in recommendations:
                if hasattr(self, "compat_db") and str(appid) in self.compat_db.get("games", {}):
                    tool_data = self.compat_db["games"][str(appid)].get("tools", {}).get(rec["method"], {})
                    metadata = self._compat_tool_metadata(rec["method"], tool_data)
                    if "warnings" in metadata:
                        rec["warnings"] = metadata["warnings"]
                    if "manual_steps" in metadata:
                        rec["manual_steps"] = metadata["manual_steps"]

            return {
                "status": "success",
                "recommendations": recommendations,
                "context": context
            }
        except Exception as e:
            decky.logger.error(f"Error in get_hdr_recommendation: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def _apply_pcgw_api_fallback(self, context: dict[str, Any], appid: str, logger=None) -> None:
        try:
            wiki_data = await asyncio.to_thread(self.wiki_scraper.get_game_data, appid)
            api = wiki_data.get("graphics_api") if isinstance(wiki_data, dict) else ""
            if not api or api == "unknown":
                return
            context["graphics_api"] = api
            context["injection_dll"] = self._api_to_injection_dll(api)
            context["api_confidence"] = "metadata"
            context["api_source"] = wiki_data.get("api_source", "pcgamingwiki_api_table")
            context["api_page"] = wiki_data.get("api_page", "")
            if logger:
                logger.info("PCGamingWiki API table detected %s for AppID %s", api, appid)
        except Exception as error:
            if logger:
                logger.info("PCGamingWiki API fallback unavailable for AppID %s: %s", appid, error)

    async def _resolve_game_executable_for_recommendation(self, appid: str, logger=None) -> str:
        try:
            detection = await self.find_game_executable_path(appid)
            steam_result = detection.get("steam_logs_result", {}) if detection.get("status") == "success" else {}
            enhanced_result = detection.get("enhanced_detection_result", {}) if detection.get("status") == "success" else {}
            exe_path = steam_result.get("executable_path") or enhanced_result.get("executable_path") or ""
            if exe_path and os.path.exists(exe_path):
                if logger:
                    logger.info(f"Resolved executable internally for recommendation: {exe_path}")
                return exe_path
            if logger:
                logger.warning(f"Could not resolve executable internally for recommendation: {detection.get('message', detection)}")
        except Exception as error:
            if logger:
                logger.warning(f"Internal executable resolution failed: {error}")
        return ""

    async def _refresh_renodx_recommendation_context(self, context: dict[str, Any], title: str, logger=None) -> None:
        try:
            renodx_result = await self.check_renodx_support(title, str(context.get("engine", "")))
            context["renodx_supported"] = bool(renodx_result.get("supported"))
            if context["renodx_supported"]:
                context["renodx_match"] = renodx_result.get("match", {})
                match_type = context["renodx_match"].get("match_type", "")
                context["renodx_flow_enabled"] = True
                if match_type == "generic_engine":
                    context["renodx_experimental"] = True
                if logger:
                    logger.info(f"RenoDX match found: {renodx_result.get('match', {}).get('name', title)} ({match_type or 'unknown'})")
        except Exception as error:
            if logger:
                logger.warning(f"RenoDX support lookup failed: {error}")

    async def _apply_steam_metadata_api_fallback(self, context: dict[str, Any], appid: str, logger=None) -> None:
        metadata_api = await self._detect_api_from_steam_metadata(appid, logger)
        if metadata_api.get("status") != "success":
            return
        context["graphics_api"] = metadata_api.get("api", "unknown")
        context["injection_dll"] = metadata_api.get("injection_dll", "dxgi")
        context["engine"] = metadata_api.get("engine", context.get("engine", "unknown"))
        context["api_confidence"] = metadata_api.get("confidence", "metadata")
        context["api_source"] = metadata_api.get("source", "steam_appdetails")

    async def _detect_api_from_steam_metadata(self, appid: str, logger=None) -> dict[str, Any]:
        result = await asyncio.to_thread(self._detect_api_from_steam_metadata_sync, appid)
        if logger and result.get("status") == "success":
            logger.info(f"Steam metadata API fallback detected {result.get('api')} for AppID {appid}")
        elif logger:
            logger.info(f"Steam metadata API fallback unavailable for AppID {appid}: {result.get('message')}")
        return result

    def _detect_api_from_steam_metadata_sync(self, appid: str) -> dict[str, Any]:
        try:
            url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=pc_requirements"
            payload = self._fetch_json(url)
            if not isinstance(payload, dict):
                return {"status": "error", "message": "Steam metadata fetch failed."}
            app_data = payload.get(str(appid), {}) if isinstance(payload, dict) else {}
            data = app_data.get("data", {}) if isinstance(app_data, dict) and app_data.get("success") else {}
            requirements = data.get("pc_requirements", {}) if isinstance(data, dict) else {}
            text = " ".join(str(requirements.get(key, "")) for key in ["minimum", "recommended"])
            api = self._api_from_requirement_text(text)
            if api == "unknown":
                return {"status": "error", "message": "Steam metadata did not include a DirectX requirement."}
            return {
                "status": "success",
                "api": api,
                "architecture": "64",
                "injection_dll": self._api_to_injection_dll(api),
                "engine": "unknown",
                "confidence": "metadata",
                "source": "steam_appdetails",
            }
        except Exception as error:
            return {"status": "error", "message": str(error)}

    def _api_from_requirement_text(self, text: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", text).lower()
        directx_versions = [int(match) for match in re.findall(r"directx\s*:?\s*(?:version\s*)?([0-9]{1,2})", clean)]
        if not directx_versions:
            directx_versions = [int(match) for match in re.findall(r"\bdx\s*([0-9]{1,2})\b", clean)]
        if not directx_versions:
            return "unknown"
        version = max(directx_versions)
        if version >= 12:
            return "d3d12"
        if version == 11:
            return "d3d11"
        if version == 10:
            return "dx10"
        if version == 9:
            return "d3d9"
        return "unknown"

    async def _detect_api_with_cache(self, game_path: str, logger=None) -> dict[str, Any]:
        api_info = await self._detect_api_for_path(game_path)
        api = api_info.get("api", "unknown")
        if logger:
            logger.info(f"Detected API without persistent cache: {api}")
        return api_info

    async def set_special_k_verified(self, appid: str, verified: bool = True) -> dict:
        try:
            self.persistent_cache.set(f"specialk_verified_{appid}", bool(verified))
            logger = setup_per_game_logger(appid)
            logger.info(f"User override: special_k_verified={bool(verified)}")
            return {
                "status": "success",
                "message": "Special K HDR marked as verified for this game." if verified else "Special K HDR verification cleared for this game.",
            }
        except Exception as e:
            decky.logger.error(f"Failed to update Special K verification override: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def reset_plugin_caches(self) -> dict:
        try:
            removed = []
            self.executable_cache.clear()
            self._cached_update_status = None
            self._last_check_time = 0.0
            self.persistent_cache.clear()
            for path in [
                Path(self.main_path) / "renodx_mods_cache.json",
                Path(decky.DECKY_PLUGIN_DIR) / ".last_update_check",
            ]:
                try:
                    if path.exists():
                        path.unlink()
                        removed.append(str(path))
                except OSError as error:
                    decky.logger.warning("Could not remove cache file %s: %s", path, error)
            decky.logger.info("Decky RenoDX caches reset")
            return {
                "status": "success",
                "message": "Detection and update caches reset. Refresh the selected game to re-detect.",
                "removed": removed,
            }
        except Exception as e:
            decky.logger.error(f"Failed to reset caches: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def get_plugin_process_health(self) -> dict:
        try:
            processes = self._plugin_processes()
            active = [p for p in processes if p.get("state") != "Z"]
            duplicate = len(active) > 1
            return {
                "status": "success",
                "duplicate": duplicate,
                "count": len(active),
                "processes": processes,
                "message": "Multiple Decky RenoDX backend processes are running." if duplicate else "Decky RenoDX process health looks normal.",
            }
        except Exception as error:
            return {"status": "error", "duplicate": False, "count": 0, "processes": [], "message": str(error)}

    async def fix_plugin_processes(self) -> dict:
        try:
            processes = self._plugin_processes()
            active = [p for p in processes if p.get("state") != "Z"]
            if len(active) <= 1:
                return {"status": "success", "message": "No duplicate Decky RenoDX processes were found.", "killed": []}
            active.sort(key=lambda p: int(p.get("etimes", 0)))
            keep = active[0]
            killed = []
            for process in active[1:]:
                pid = int(process["pid"])
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except OSError as error:
                    decky.logger.warning("Could not terminate duplicate plugin process %s: %s", pid, error)
            return {"status": "success", "message": f"Kept PID {keep['pid']} and asked {len(killed)} duplicate process(es) to stop.", "killed": killed}
        except Exception as error:
            return {"status": "error", "message": str(error), "killed": []}

    def _plugin_processes(self) -> list[dict[str, Any]]:
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,stat=,etimes=,pcpu=,pmem=,args="],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return []
        processes = []
        for line in result.stdout.splitlines():
            if "decky-renodx/main.py" not in line and "Decky RenoDX" not in line:
                continue
            parts = line.strip().split(None, 6)
            if len(parts) < 7:
                continue
            pid, ppid, stat, etimes, pcpu, pmem, args = parts
            try:
                int(pid)
            except ValueError:
                continue
            processes.append({
                "pid": pid,
                "ppid": ppid,
                "state": stat[:1],
                "etimes": etimes,
                "cpu": pcpu,
                "mem": pmem,
                "args": args,
            })
        return processes

    async def get_per_game_log(self, appid: str) -> dict:
        """Read and return the content of the per-game log."""
        try:
            log_path = get_game_log_path(appid)
            plugin_log = ""
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    plugin_log = f.read()[-120000:]
            proton = self._read_proton_log(appid)
            if not plugin_log and not proton.get("log"):
                return {"status": "error", "message": "Log file not found."}
            return {
                "status": "success",
                "log": plugin_log[-30000:],
                "plugin_log": plugin_log,
                "proton_log": proton.get("log", ""),
                "proton_log_path": proton.get("path", ""),
                "path": log_path,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_pcgw_improvements_issues(self, appid: str) -> dict:
        return await asyncio.to_thread(self.wiki_scraper.get_improvements_and_issues, appid)

    def _read_proton_log(self, appid: str) -> dict[str, str]:
        candidates = [
            self._deck_user_home() / f"steam-{appid}.log",
            self._deck_user_home() / ".local" / "share" / "Steam" / f"steam-{appid}.log",
        ]
        found = [path for path in candidates if path.exists()]
        if not found:
            found = sorted(self._deck_user_home().glob(f"steam-{appid}.log"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not found:
            return {"path": "", "log": ""}
        path = found[0]
        return {"path": str(path), "log": path.read_text(encoding="utf-8", errors="ignore")[-180000:]}

    async def execute_setup_flow(self, appid: str, title: str, exe_path: str = "", skip_current: bool = False, clear_compatdata: bool = True) -> dict:
        """Execute the automated setup flow based on the decision tree."""
        async with self._install_lock:
            try:
                logger = setup_per_game_logger(appid)
                logger.info(f"Starting automated setup for {title} (skip_current={skip_current})")
                if clear_compatdata:
                    compat_result = self._clear_steam_compatdata(appid, logger)
                    if compat_result["status"] == "error":
                        return {"status": "error", "message": compat_result["message"], "compatdata": compat_result}

                # 1. Get Recommendations
                rec_result = await self.get_hdr_recommendation(appid, title, exe_path)
                if rec_result["status"] != "success":
                    return rec_result

                recommendations = rec_result["recommendations"]
                context = rec_result.get("context", {}) or {}
                
                # If skip_current, remove the top recommendation if it's already installed
                if skip_current and len(recommendations) > 1:
                    logger.info(f"Skipping top recommendation: {recommendations[0]['method']}")
                    recommendations = recommendations[1:]

                # 2. Iterate through recommendations until one works
                for rec in recommendations:
                    method = rec["method"]
                    if method == "sdr":
                        logger.info("Falling back to SDR (no safe HDR method found).")
                        return {"status": "success", "method": "sdr", "message": "No safe HDR method found. Using SDR."}

                    logger.info(f"Attempting to install: {method}")
                    
                    if method == "native_hdr":
                        return {"status": "success", "method": "native_hdr", "message": "Game has native HDR support. No injection needed."}

                    if method == "renodx":
                        mod = await self._resolve_renodx_match_for_install(appid, title, exe_path, context, logger)
                        if mod:
                            result = await self._install_renodx_mod_for_game(appid, title, exe_path, mod, logger)
                            if result.get("status") == "success":
                                return result
                            if result.get("manual_download"):
                                return result
                            logger.error(f"RenoDX install failed: {result.get('message')}")
                        continue

                    if method == "special_k":
                        gate = self._specialk_local_install_gate(appid)
                        if not gate["available"]:
                            logger.error("Special K blocked by compatibility gate: %s", gate["reason"])
                            continue
                        await self._ensure_special_k_bin()
                        api_info = await self._detect_api_with_cache(exe_path, logger) if exe_path and os.path.exists(exe_path) else {}
                        arch = str(api_info.get("architecture", "64") or "64")
                        exe_dir = self._compat_specialk_install_dir(appid, Path(exe_path).parent) if exe_path else Path()
                        sk_dll = self._specialk_dll_for_game(appid, context, str(api_info.get("injection_dll") or api_info.get("api") or "dxgi"))
                        specialk_result = self._install_specialk_for_game(exe_dir, sk_dll, arch, appid)
                        if specialk_result.get("status") != "success":
                            logger.error(f"Special K install failed: SpecialK{arch}.dll was not found after runtime setup.")
                            continue

                        launch_opts = self._hdr_launch_options(str(specialk_result["dll"]), appid, "special_k")
                        await self._set_steam_launch_options(appid, launch_opts)
                        self._write_game_hdr_marker(exe_dir, appid, "specialk", str(specialk_result["dll"]), arch)
                        self._fix_deck_user_ownership(exe_dir)
                        return {
                            "status": "success", 
                            "method": "special_k", 
                            "message": f"Special K installed as {specialk_result['dll']}.dll. HDR still needs in-game verification.",
                            "launch_options": launch_opts
                        }

                    if method == "reshade":
                        reshade_dll = str(context.get("injection_dll") or "dxgi")
                        if reshade_dll == "auto":
                            reshade_dll = "dxgi"
                        reshade_result = await self.manage_game_reshade(appid, "install", reshade_dll, "", exe_path)
                        if reshade_result["status"] == "success":
                            launch_opts = self._hdr_launch_options(reshade_dll, appid, "reshade")
                            return {
                                "status": "success", 
                                "method": "reshade", 
                                "message": "ReShade AutoHDR installed.",
                                "launch_options": launch_opts
                            }
                        else:
                            logger.error(f"ReShade install failed: {reshade_result['message']}")
                            continue
                    
                return {"status": "error", "message": "All HDR methods failed."}
            except Exception as e:
                decky.logger.error(f"Error in execute_setup_flow: {str(e)}")
                return {"status": "error", "message": str(e)}

    async def install_selected_hdr_method(self, appid: str, title: str, exe_path: str = "", method: str = "recommended") -> dict:
        """Remove the current HDR install, then install the user-selected method."""
        method = (method or "recommended").strip().lower()
        if method == "specialk":
            method = "special_k"
        if method == "reshade_autohdr":
            method = "reshade"
        valid_methods = {"recommended", "native_hdr", "renodx", "special_k", "special_k_delayed", "reshade", "sdr"}
        if method not in valid_methods:
            return {"status": "error", "message": f"Unsupported HDR method: {method}"}

        logger = setup_per_game_logger(appid)
        logger.info("Installing selected HDR method for %s: %s", title, method)
        rec_result = await self.get_hdr_recommendation(appid, title, exe_path)
        context = rec_result.get("context", {}) if rec_result.get("status") == "success" else {}
        options = {str(item.get("method")): item for item in context.get("method_options", []) if isinstance(item, dict)}
        option = options.get(method)
        if option and not option.get("available", True):
            return {
                "status": "error",
                "method": method,
                "message": f"{option.get('label', method)} is blocked: {option.get('reason', 'Not available for this game.')}",
                "option": option,
            }

        uninstall_result = await self.run_surgical_uninstall(appid, exe_path)
        if uninstall_result.get("status") == "error":
            return {
                "status": "error",
                "message": f"Could not remove current HDR install before applying {method}: {uninstall_result.get('message')}",
                "uninstall": uninstall_result,
            }

        if method == "sdr":
            return {
                "status": "success",
                "method": "sdr",
                "message": "HDR removed. This game is now set to SDR/no injection.",
                "uninstall": uninstall_result,
            }

        if method == "native_hdr":
            return {
                "status": "success",
                "method": "native_hdr",
                "message": "Injection removed. Use the game's native HDR settings.",
                "uninstall": uninstall_result,
            }

        if method == "recommended":
            # run_surgical_uninstall above already cleared compatdata.
            result = await self.execute_setup_flow(appid, title, exe_path, False, clear_compatdata=False)
            result["uninstall"] = uninstall_result
            return result

        if method == "renodx":
            mod = await self._resolve_renodx_match_for_install(appid, title, exe_path, context, logger)
            if not mod:
                return {"status": "error", "method": "renodx", "message": "No RenoDX/Luma mod matched this game or detected engine.", "uninstall": uninstall_result}
            result = await self._install_renodx_mod_for_game(appid, title, exe_path, mod, logger)
            result["uninstall"] = uninstall_result
            return result

        if method == "special_k":
            gate = self._specialk_local_install_gate(appid)
            if not gate["available"]:
                return {"status": "error", "method": "special_k", "message": gate["reason"], "uninstall": uninstall_result, "gate": gate}
            result = await self.force_special_k_setup(appid, title, exe_path, clear_compatdata=False)
            result["uninstall"] = uninstall_result
            return result

        if method == "special_k_delayed":
            result = await self.install_special_k_delayed_global(appid, title, exe_path)
            result["uninstall"] = uninstall_result
            return result

        if method == "reshade":
            reshade_dll = str(context.get("injection_dll") or "dxgi")
            if reshade_dll == "auto":
                reshade_dll = "dxgi"
            result = await self.manage_game_reshade(appid, "install", reshade_dll, "", exe_path)
            if result.get("status") == "success":
                result["method"] = "reshade"
                result["message"] = result.get("message") or "ReShade AutoHDR installed."
            result["uninstall"] = uninstall_result
            return result

        return {"status": "error", "message": f"Unhandled HDR method: {method}", "uninstall": uninstall_result}

    async def force_special_k_setup(self, appid: str, title: str, exe_path: str = "", clear_compatdata: bool = True) -> dict:
        """Install Special K even when a higher-priority RenoDX/Luma recommendation exists."""
        async with self._install_lock:
            try:
                logger = setup_per_game_logger(appid)
                logger.info(f"User requested Special K override for {title}")
                if clear_compatdata:
                    compat_result = self._clear_steam_compatdata(appid, logger)
                    if compat_result["status"] == "error":
                        return {"status": "error", "message": compat_result["message"], "compatdata": compat_result}
                if not exe_path or not os.path.exists(exe_path):
                    return {"status": "error", "message": "Game executable path was not resolved. Refresh the game state and try again."}

                await self._ensure_special_k_bin()
                api_info = await self._detect_api_with_cache(exe_path, logger)
                arch = str(api_info.get("architecture", "64") or "64")

                rec_result = await self.get_hdr_recommendation(appid, title, exe_path)
                context = rec_result.get("context", {}) if rec_result.get("status") == "success" else {}
                exe_dir = self._compat_specialk_install_dir(appid, Path(exe_path).parent)
                gate = self._specialk_local_install_gate(appid)
                if not gate["available"]:
                    return {"status": "error", "message": gate["reason"], "gate": gate}
                sk_dll = self._specialk_dll_for_game(appid, context, str(api_info.get("injection_dll") or api_info.get("api") or "dxgi"))
                specialk_result = self._install_specialk_for_game(exe_dir, sk_dll, arch, appid)
                if specialk_result.get("status") != "success":
                    message = str(specialk_result.get("message") or "Special K install failed.")
                    logger.error(f"Forced Special K install failed: {message}")
                    return {"status": "error", "message": message}

                launch_opts = self._hdr_launch_options(str(specialk_result["dll"]), appid, "special_k")
                await self._set_steam_launch_options(appid, launch_opts)
                logger.info("Forced Special K install completed; HDR still requires in-game verification.")
                return {
                    "status": "success",
                    "method": "special_k",
                    "message": f"Special K installed as {specialk_result['dll']}.dll. HDR still needs in-game verification.",
                    "launch_options": launch_opts,
                }
            except Exception as e:
                decky.logger.error(f"Error in force_special_k_setup: {str(e)}")
                return {"status": "error", "message": str(e)}

    async def install_special_k_delayed_global(self, appid: str, title: str, exe_path: str = "") -> dict:
        """Experimental Special K global/delayed injection through a launch wrapper."""
        try:
            logger = setup_per_game_logger(appid)
            if not exe_path or not os.path.exists(exe_path):
                return {"status": "error", "method": "special_k_delayed", "message": "Game executable path was not resolved. Refresh the game state and try again."}
            await self._ensure_special_k_bin()
            specialk_dir = Path(self.main_path) / "SpecialK"
            injector = self._specialk_global_injector()
            if not injector:
                return {"status": "error", "method": "special_k_delayed", "message": "Special K runtime does not include a supported global injector executable. Local DLL injection remains blocked for this game."}

            compat_path = self._steam_compatdata_paths(appid)[0] if self._steam_compatdata_paths(appid) else Path(self._deck_expanduser(f"~/.local/share/Steam/steamapps/compatdata/{appid}"))
            sk_prefix_dir = compat_path / "pfx" / "drive_c" / "users" / "steamuser" / "Documents" / "My Mods" / "SpecialK"
            sk_prefix_dir.mkdir(parents=True, exist_ok=True)
            for item in specialk_dir.iterdir():
                target = sk_prefix_dir / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                elif item.is_file():
                    shutil.copy2(item, target)
            prefix_injector = sk_prefix_dir / injector.name
            ini = sk_prefix_dir / "SpecialK.ini"
            self._write_specialk_hdr_ini(ini, appid=appid)
            delay = self._compat_specialk_delay(appid)
            self._upsert_specialk_global_profile(sk_prefix_dir / "Profiles.ini", title, exe_path, delay)
            wrapper = Path(decky.DECKY_PLUGIN_DIR) / "assets" / "specialk-delayed-launch.sh"
            launch_opts = f'STEAM_COMPAT_DATA_PATH="{compat_path}" PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 bash "{wrapper}" "{appid}" "{delay}" "{prefix_injector}" %command%'
            await self._set_steam_launch_options(appid, launch_opts)
            exe_dir = Path(exe_path).parent
            self._write_game_hdr_marker(exe_dir, appid, "specialk-delayed", "global", "64", {"specialk_prefix": str(sk_prefix_dir), "delay": delay})
            self.manifest_manager.write_manifest(appid, {
                "appid": appid,
                "title": title,
                "method": "special_k_delayed",
                "installed_files": [str(sk_prefix_dir), str(exe_dir / ".decky-renodx-hdr.json")],
                "modified_files": [],
                "backups": {},
                "launch_options_after": launch_opts,
                "verified": False,
                "verification_notes": "Experimental Special K delayed/global injection configured. Requires in-game verification.",
                "plugin_version": self._current_version(),
            })
            self._fix_deck_user_ownership(sk_prefix_dir)
            self._fix_deck_user_ownership(exe_dir)
            logger.info("Configured Special K delayed/global injection for %s with %ss delay.", appid, delay)
            return {"status": "success", "method": "special_k_delayed", "message": f"Configured experimental Special K delayed injection ({delay}s). Launch the game once and verify the Special K HDR menu.", "launch_options": launch_opts}
        except Exception as e:
            decky.logger.error(f"Error in install_special_k_delayed_global: {str(e)}")
            return {"status": "error", "method": "special_k_delayed", "message": str(e)}

    async def _install_renodx_mod_for_game(self, appid: str, title: str, exe_path: str, mod: dict[str, Any], logger=None) -> dict:
        addon_url = str(mod.get("addon_url") or "")
        manual_url = str(mod.get("manual_url") or "")
        if not addon_url:
            return {
                "status": "manual_required",
                "method": "renodx",
                "manual_download": True,
                "url": manual_url or ((mod.get("pageLinks") or [""])[0]),
                "message": "RenoDX support was found, but this entry requires a manual download. Open the linked page, download the addon/archive, then import it.",
                "match": mod,
            }

        if not exe_path or not os.path.exists(exe_path):
            exe_path = await self._resolve_game_executable_for_recommendation(appid, logger)
        if not exe_path or not os.path.exists(exe_path):
            return {
                "status": "error",
                "method": "renodx",
                "message": "Could not resolve the game executable for RenoDX install.",
                "match": mod,
            }

        exe_dir = self._resolve_game_exe_dir(appid, exe_path)
        if (exe_dir / ".decky-renodx-hdr.json").exists() or (exe_dir / "ReShade.ini").exists():
            for stale_dll in ["dxgi", "d3d11", "d3d12", "d3d9", "d3d8", "ddraw", "dinput8", "opengl32"]:
                stale_path = exe_dir / f"{stale_dll}.dll"
                if stale_path.exists() or stale_path.is_symlink():
                    try:
                        stale_path.unlink()
                    except OSError as error:
                        if logger:
                            logger.warning("Could not remove stale RenoDX proxy before API detection: %s (%s)", stale_path, error)
        api = "dxgi"
        try:
            detected = await self._detect_api_for_path(str(exe_dir))
            if detected.get("status") == "success":
                api = self._api_to_injection_dll(str(detected.get("api") or api))
        except Exception:
            pass
        if api in {"auto", "unknown", "vulkan"}:
            api = "dxgi"
        if str(mod.get("bitness", "")).strip() == "64":
            api = "dxgi"

        reshade_result = await self.manage_game_reshade(appid, "install", api, "", exe_path)
        if reshade_result.get("status") != "success":
            self._rollback_failed_hdr_install(appid, exe_dir, logger)
            return {
                "status": "error",
                "method": "renodx",
                "message": f"ReShade prerequisite install failed before RenoDX addon copy: {reshade_result.get('message')}",
                "match": mod,
            }

        addon_cache = Path(self.renodx_import_path) / "downloads"
        addon_cache.mkdir(parents=True, exist_ok=True)
        addon_name = self._renodx_addon_filename(addon_url, title)
        target_cache = addon_cache / addon_name
        download_error: Exception | None = None
        for candidate_url in self._renodx_addon_url_candidates(addon_url):
            try:
                await asyncio.to_thread(self._download_url, candidate_url, target_cache)
                if not target_cache.exists() or target_cache.stat().st_size < 1024:
                    raise RuntimeError(f"Downloaded addon is missing or too small: {target_cache}")
                download_error = None
                if candidate_url != addon_url and logger:
                    logger.info("RenoDX addon downloaded from fallback mirror: %s", candidate_url)
                break
            except Exception as error:
                download_error = error
                if logger:
                    logger.warning("RenoDX addon download failed for %s -> %s: %s", candidate_url, target_cache, error)
        if download_error is not None:
            self._rollback_failed_hdr_install(appid, exe_dir, logger)
            return {
                "status": "error",
                "method": "renodx",
                "message": f"RenoDX snapshot download failed: {addon_url} ({download_error})",
                "match": mod,
            }

        target = exe_dir / addon_name
        try:
            shutil.copy2(target_cache, target)
        except Exception as error:
            if logger:
                logger.error("RenoDX addon copy failed from %s to %s: %s", target_cache, target, error)
            self._rollback_failed_hdr_install(appid, exe_dir, logger)
            return {
                "status": "error",
                "method": "renodx",
                "message": f"RenoDX addon copy failed: {target_cache} -> {target} ({error})",
                "match": mod,
            }
        removed_effects = self._configure_renodx_only_install(exe_dir, addon_name, logger)

        # RenoDX Commander parity: install the Display Commander companion
        # addon next to the mod. Failure is non-fatal; the mod works without it.
        display_commander_target = ""
        if addon_name.lower().endswith(".addon64"):
            dc_cache = await asyncio.to_thread(self._ensure_display_commander_cached)
            if dc_cache:
                try:
                    shutil.copy2(dc_cache, exe_dir / DISPLAY_COMMANDER_ADDON_NAME)
                    display_commander_target = str(exe_dir / DISPLAY_COMMANDER_ADDON_NAME)
                    if logger:
                        logger.info("Installed Display Commander companion addon: %s", display_commander_target)
                except OSError as error:
                    if logger:
                        logger.warning("Could not copy Display Commander addon: %s", error)
            elif logger:
                logger.warning("Display Commander addon unavailable; continuing with RenoDX only.")
        self._fix_deck_user_ownership(exe_dir)

        injection_dll = str(reshade_result.get("injection_dll") or api)
        launch_options = self._hdr_launch_options(injection_dll, appid, "renodx")
        await self._set_steam_launch_options(appid, launch_options)
        arch = "64" if addon_name.lower().endswith(".addon64") else "32" if addon_name.lower().endswith(".addon32") else "unknown"
        marker_extra: dict[str, Any] = {"renodx_match": mod, "renodx_addon": str(target)}
        if display_commander_target:
            marker_extra["display_commander"] = display_commander_target
        self._write_game_hdr_marker(
            exe_dir,
            appid,
            "renodx",
            injection_dll,
            arch,
            marker_extra,
        )

        manifest = self.manifest_manager.read_manifest(appid) or {"appid": appid, "installed_files": [], "modified_files": [], "backups": {}}
        retained_existing = [
            path for path in manifest.get("installed_files", [])
            if Path(path).exists() and Path(path).name not in removed_effects
        ]
        new_installed = [*retained_existing, str(target), str(exe_dir / ".decky-renodx-hdr.json")]
        if display_commander_target:
            new_installed.append(display_commander_target)
        manifest.update({
            "appid": appid,
            "title": title,
            "method": "renodx",
            "installed_files": list(dict.fromkeys(new_installed)),
            "launch_options_after": launch_options,
            "renodx_match": mod,
            "verified": True,
            "verification_notes": "RenoDX addon downloaded and copied alongside a clean ReShade addon host.",
            "plugin_version": self._current_version(),
        })
        self.manifest_manager.write_manifest(appid, manifest)
        if logger:
            logger.info("Installed RenoDX addon %s for %s from %s", target, appid, addon_url)
        copied = [str(target)]
        if display_commander_target:
            copied.append(display_commander_target)
        return {
            "status": "success",
            "method": "renodx",
            "message": (
                f"Installed RenoDX addon for {mod.get('name', title)}"
                + (" with Display Commander." if display_commander_target else ".")
            ),
            "copied": copied,
            "launch_options": launch_options,
            "match": mod,
        }

    def _ensure_display_commander_cached(self) -> Path | None:
        """Download and cache the Display Commander companion addon.

        RenoDX Commander installs this alongside every RenoDX mod; newer mods
        assume it is present. A stale cache is reused when the download fails.
        """
        cached = Path(self.bin_cache_path) / DISPLAY_COMMANDER_ADDON_NAME
        fresh = cached.exists() and cached.stat().st_size > 1024 and time.time() - cached.stat().st_mtime < 7 * 86400
        if not fresh:
            temp = cached.with_suffix(".addon64.tmp")
            try:
                self._download_url(DISPLAY_COMMANDER_ADDON_URL, temp)
                if temp.exists() and temp.stat().st_size > 1024:
                    temp.replace(cached)
            except Exception as error:
                decky.logger.warning("Display Commander download failed: %s", error)
            finally:
                temp.unlink(missing_ok=True)
        if cached.exists() and cached.stat().st_size > 1024:
            return cached
        return None

    # Known mirrors per addon filename, matching RenoDX Commander's overrides.
    _RENODX_ADDON_URL_OVERRIDES: dict[str, list[str]] = {
        "renodx-ue-extended.addon64": [
            "https://marat569.github.io/renodx/renodx-ue-extended.addon64",
        ],
        "renodx-unityengine.addon64": [
            "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon64",
            "https://clshortfuse.github.io/renodx/renodx-unityengine.addon64",
        ],
        "renodx-unityengine.addon32": [
            "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon32",
            "https://clshortfuse.github.io/renodx/renodx-unityengine.addon32",
        ],
    }

    def _renodx_addon_url_candidates(self, addon_url: str) -> list[str]:
        candidates = [addon_url]
        name = Path(urllib.parse.unquote(urllib.parse.urlparse(addon_url).path)).name.lower()
        candidates.extend(self._RENODX_ADDON_URL_OVERRIDES.get(name, []))
        # The official snapshot release mirrors every github.io addon build.
        if re.search(r"\.addon(?:32|64)$", name) and "github.io" in addon_url.lower():
            candidates.append(f"https://github.com/clshortfuse/renodx/releases/download/snapshot/{name}")
        return list(dict.fromkeys(candidates))

    def _renodx_addon_filename(self, addon_url: str, title: str) -> str:
        parsed = urllib.parse.urlparse(addon_url)
        name = Path(urllib.parse.unquote(parsed.path)).name
        if not re.search(r"\.addon(?:32|64)?$", name.lower()):
            bitness = "64" if "64" in addon_url else "32" if "32" in addon_url else "64"
            name = f"renodx-{self._normalize_game_title(title)}.addon{bitness}"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name)

    def _rollback_failed_hdr_install(self, appid: str, exe_dir: Path, logger=None) -> None:
        try:
            self.manifest_manager.remove_hdr(appid, logger)
        except Exception as error:
            if logger:
                logger.warning("Manifest rollback failed for %s: %s", appid, error)
        for name in [".decky-renodx-hdr.json", "ReShade.ini", "ReShadePreset.ini", "dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll"]:
            path = exe_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError as error:
                if logger:
                    logger.warning("Rollback could not remove %s: %s", path, error)
        for path in [exe_dir / "ReShade_shaders", *exe_dir.glob("*.addon*")]:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError as error:
                if logger:
                    logger.warning("Rollback could not remove %s: %s", path, error)
        cleanup = self._cleanup_hdr_files_without_manifest(appid, "", logger)
        self._reset_game_cached_state(appid)
        if logger:
            logger.info("Rolled back failed HDR install for %s: %s", appid, cleanup.get("message"))

    def _configure_renodx_only_install(self, exe_dir: Path, addon_name: str, logger=None) -> set[str]:
        """Remove fallback shader effects from a RenoDX install.

        RenoDX uses ReShade as an addon host. For RenoDX installs we should not
        also enable AutoHDR/Lilium/Pumbo shaders, because that can double tone
        map or confuse the ReShade UI.
        """
        removed: set[str] = set()
        for pattern in [
            "AutoHDR*.addon",
            "AutoHDR.addon*",
            "AdvancedAutoHDR.fx",
            "ConvertColorSpace.fx",
            "lilium*.fx",
            "lilium*.fxh",
        ]:
            for path in exe_dir.glob(pattern):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    removed.add(path.name)
                    if logger:
                        logger.info("Removed non-RenoDX effect from RenoDX install: %s", path)
                except OSError as error:
                    if logger:
                        logger.warning("Could not remove non-RenoDX effect %s: %s", path, error)
        shader_dir = exe_dir / "ReShade_shaders"
        if shader_dir.exists():
            try:
                shutil.rmtree(shader_dir)
                removed.add(shader_dir.name)
                if logger:
                    logger.info("Removed shader directory from RenoDX-only install: %s", shader_dir)
            except OSError as error:
                if logger:
                    logger.warning("Could not remove shader directory %s: %s", shader_dir, error)

        preset = exe_dir / "ReShadePreset.ini"
        preset.write_text("Techniques=\nTechniqueSorting=\n", encoding="utf-8")

        ini = exe_dir / "ReShade.ini"
        if ini.exists():
            text = ini.read_text(encoding="utf-8", errors="ignore")
        else:
            text = "[GENERAL]\n"
        text = self._upsert_ini_section_values(text, "GENERAL", {
            "EffectSearchPaths": "",
            "TextureSearchPaths": "",
            "PresetPath": ".\\ReShadePreset.ini",
            "TutorialProgress": "4",
            "SkipLoadingDisabledEffects": "1",
        })
        text = self._upsert_ini_section_values(text, "ADDON", {
            "LoadFromDllMain": addon_name,
        })
        ini.write_text(text, encoding="utf-8")
        return removed

    async def _ensure_special_k_bin(self):
        """Ensure Special K binaries are present in the cache."""
        try:
            bin_dir = Path(self.bin_cache_path)
            specialk_archive = bin_dir / "SpecialK.7z"
            if not specialk_archive.exists() or specialk_archive.stat().st_size < 1024 * 1024:
                await asyncio.to_thread(self._download_latest_github_asset, SPECIALK_RELEASES_URL, specialk_archive, [".7z", ".zip"])

            specialk_dir = Path(self.main_path) / "SpecialK"
            if not specialk_dir.exists():
                await asyncio.to_thread(self._install_specialk_runtime, Path(self.main_path), bin_dir)
        except Exception as e:
            decky.logger.error(f"Failed to ensure Special K binaries: {str(e)}")

    def _specialk_runtime_source(self, arch: str = "64") -> Path | None:
        specialk_dir = Path(self.main_path) / "SpecialK"
        source_name = "SpecialK32.dll" if str(arch) == "32" else "SpecialK64.dll"
        if not specialk_dir.exists():
            return None
        candidates = sorted(specialk_dir.rglob(source_name))
        return candidates[0] if candidates else None

    def _specialk_global_injector(self) -> Path | None:
        specialk_dir = Path(self.main_path) / "SpecialK"
        if not specialk_dir.exists():
            return None
        preferred = ["SpecialK.exe", "SKIF.exe", "SpecialK64.exe", "SpecialK32.exe"]
        for name in preferred:
            matches = sorted(specialk_dir.rglob(name))
            if matches:
                return matches[0]
        matches = sorted(path for path in specialk_dir.rglob("*.exe") if "special" in path.name.lower() or "skif" in path.name.lower())
        return matches[0] if matches else None

    async def _set_steam_launch_options(self, appid: str, options: str):
        """Best-effort backend launch option writer for Steam games."""
        await asyncio.to_thread(self._set_steam_launch_options_sync, appid, options)

    def _clear_hdr_launch_options_sync(self, appid: str) -> dict[str, Any]:
        try:
            existing = self._steam_launch_options(appid)
            cleaned = self._strip_hdr_launch_tokens(existing)
            if cleaned == existing:
                return {"status": "noop", "message": "No HDR launch options were present.", "before": existing, "after": cleaned}
            self._set_steam_launch_options_sync(appid, cleaned)
            return {"status": "success", "message": "Removed HDR launch options.", "before": existing, "after": cleaned}
        except Exception as error:
            decky.logger.warning("Could not clear HDR launch options for %s: %s", appid, error)
            return {"status": "error", "message": f"Could not clear HDR launch options: {error}", "before": "", "after": ""}

    def _strip_hdr_launch_tokens(self, options: str) -> str:
        text = options or ""
        text = re.sub(r"\b(?:PROTON_ENABLE_HDR|DXVK_HDR|ENABLE_HDR_WSI|ENABLE_GAMESCOPE_WSI|PROTON_LOG)=\S+\s*", "", text)
        # Special K delayed/global injection wrapper and its compat-path prefix.
        text = re.sub(r'\bSTEAM_COMPAT_DATA_PATH="[^"]*"\s*', "", text)
        text = re.sub(r"\bSTEAM_COMPAT_DATA_PATH=\S+\s*", "", text)
        text = re.sub(r'\bbash\s+"[^"]*specialk-delayed-launch\.sh"(?:\s+"[^"]*")*\s*', "", text)
        text = re.sub(r'\bWINEDLLOVERRIDES="[^"]*(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)[^"]*"\s*', "", text)
        text = re.sub(r"\bWINEDLLOVERRIDES=\S*(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)\S*\s*", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _set_steam_launch_options_sync(self, appid: str, options: str) -> None:
        if not re.fullmatch(r"\d+", str(appid or "")):
            raise ValueError(f"Invalid appid: {appid}")
        escaped = (options or "").replace("\\", "\\\\").replace('"', '\\"')
        updated = False
        for localconfig in self._steam_localconfig_candidates():
            if not localconfig.exists():
                continue
            text = localconfig.read_text(encoding="utf-8", errors="ignore")
            new_text, changed = self._replace_localconfig_launch_options(text, str(appid), escaped)
            if changed:
                localconfig.write_text(new_text, encoding="utf-8")
                updated = True
        if not updated:
            decky.logger.warning("Could not find Steam localconfig launch options block for appid %s", appid)

    def _steam_localconfig_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for steam_root in self._steam_root_candidates():
            userdata = steam_root / "userdata"
            if not userdata.exists():
                continue
            candidates.extend(userdata.glob("*/config/localconfig.vdf"))
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path.resolve())
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _replace_localconfig_launch_options(self, text: str, appid: str, escaped_options: str) -> tuple[str, bool]:
        needle = f'"{appid}"'
        pos = 0
        changed_any = False
        while True:
            idx = text.find(needle, pos)
            if idx < 0:
                return text, changed_any
            brace = text.find("{", idx)
            if brace < 0:
                return text, False
            depth = 0
            end = None
            for offset in range(brace, len(text)):
                if text[offset] == "{":
                    depth += 1
                elif text[offset] == "}":
                    depth -= 1
                    if depth == 0:
                        end = offset + 1
                        break
            if end is None:
                return text, False
            block = text[idx:end]
            if '"LaunchOptions"' in block:
                new_block = re.sub(
                    r'(?m)^(\s*"LaunchOptions"\s+").*("\s*)$',
                    lambda match: f"{match.group(1)}{escaped_options}{match.group(2)}",
                    block,
                    count=1,
                )
                if new_block != block:
                    text = text[:idx] + new_block + text[end:]
                    end = idx + len(new_block)
                    changed_any = True
                pos = end
                continue
            insert_at = block.rfind("}")
            if insert_at >= 0:
                indent = re.search(r'(?m)^(\s*)"[^"]+"\s+"[^"]*"\s*$', block)
                prefix = indent.group(1) if indent else "\t\t\t"
                line = f'{prefix}"LaunchOptions"\t\t"{escaped_options}"\n'
                new_block = block[:insert_at] + line + block[insert_at:]
                text = text[:idx] + new_block + text[end:]
                pos = idx + len(new_block)
                changed_any = True
                continue
            pos = end

    async def update_sk_config_value(self, appid: str, exe_path: str, section: str, key: str, value: str) -> dict:
        """Update a specific value in SpecialK.ini."""
        try:
            game_dir = os.path.dirname(exe_path)
            ini_path = os.path.join(game_dir, "SpecialK.ini")
            if not os.path.exists(ini_path):
                return {"status": "error", "message": "SpecialK.ini not found."}

            text = Path(ini_path).read_text(encoding="utf-8", errors="ignore")
            updated_text = self._upsert_ini_section_values(text, section, {key: value})
            Path(ini_path).write_text(updated_text, encoding="utf-8")
            return {"status": "success", "message": f"Updated {key} to {value}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def run_surgical_uninstall(self, appid: str, exe_path: str = "") -> dict:
        """Remove HDR using the manifest system."""
        try:
            logger = setup_per_game_logger(appid)
            success, message = self.manifest_manager.remove_hdr(appid, logger)
            fallback = self._cleanup_hdr_files_without_manifest(appid, exe_path, logger)
            launch_result = self._clear_hdr_launch_options_sync(appid)
            compat_result = self._clear_steam_compatdata(appid, logger)
            self._reset_game_cached_state(appid)
            status_after = await self.get_game_hdr_status(appid, exe_path)
            compat_message = compat_result.get("message", "")
            launch_message = launch_result.get("message", "")
            if success and fallback["status"] != "error":
                return {"status": "success", "message": f"{message} {fallback.get('message', '')} {launch_message} {compat_message}".strip(), **fallback, "launch_options": launch_result, "compatdata": compat_result, "status_after": status_after}
            if not success and fallback["status"] == "success":
                return {**fallback, "message": f"{fallback.get('message', '')} {launch_message} {compat_message}".strip(), "launch_options": launch_result, "compatdata": compat_result, "status_after": status_after}
            if compat_result.get("status") in {"success", "noop"} and fallback["status"] != "error":
                return {
                    **fallback,
                    "status": "success",
                    "message": f"{message} {fallback.get('message', '')} {launch_message} {compat_message} Cached state reset.".strip(),
                    "launch_options": launch_result,
                    "compatdata": compat_result,
                    "status_after": status_after,
                }
            return {"status": "error", "message": f"{message} {fallback.get('message', '')} {launch_message} {compat_message}".strip(), **fallback, "launch_options": launch_result, "compatdata": compat_result, "status_after": status_after}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _reset_game_cached_state(self, appid: str) -> None:
        suffix = str(appid)
        for key in list(self.persistent_cache.data.keys()):
            if key.endswith(f"_{suffix}") or f"_{suffix}_" in key:
                self.persistent_cache.data.pop(key, None)
        self.persistent_cache._save()
        self.executable_cache.clear()

    def _clear_steam_compatdata(self, appid: str, logger=None) -> dict:
        if not re.fullmatch(r"\d+", str(appid or "")):
            return {"status": "error", "message": f"Refusing to clear compatdata for invalid appid: {appid}", "removed": [], "errors": []}
        removed = []
        errors = []
        candidates = self._steam_compatdata_paths(appid)
        for path in candidates:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if not path.exists():
                if logger:
                    logger.info(f"Compatdata path not present: {path}")
                continue
            if path.name != str(appid) or path.parent.name != "compatdata":
                errors.append(f"Refused unsafe compatdata path: {path}")
                continue
            try:
                shutil.rmtree(path)
                removed.append(str(path))
                if logger:
                    logger.info(f"Cleared Steam compatdata prefix: {path}")
            except OSError as error:
                errors.append(f"{path}: {error}")
                if logger:
                    logger.error(f"Could not clear compatdata {path}: {error}")
        if errors:
            return {"status": "error", "message": "Could not clear one or more Steam compatdata prefixes.", "removed": removed, "errors": errors}
        if removed:
            return {"status": "success", "message": f"Cleared {len(removed)} Steam compatdata prefix(es).", "removed": removed, "errors": []}
        return {"status": "noop", "message": "No Steam compatdata prefix was present to clear.", "removed": [], "errors": []}

    def _steam_compatdata_paths(self, appid: str) -> list[Path]:
        paths: list[Path] = []
        for steam_root in self._steam_root_candidates():
            path = steam_root / "steamapps" / "compatdata" / str(appid)
            if path not in paths:
                paths.append(path)
        library_file = self._find_libraryfolders_file()
        if library_file and library_file.exists():
            try:
                for library_path in self._steam_library_paths(library_file):
                    path = Path(library_path) / "steamapps" / "compatdata" / str(appid)
                    if path not in paths:
                        paths.append(path)
            except Exception as error:
                decky.logger.warning("Could not enumerate library compatdata paths: %s", error)
        return paths

    def _set_wine_dll_override(self, appid: str, dll: str, value: str = "native,builtin", logger=None) -> dict[str, Any]:
        """Persist a Wine DLL override in the Steam compatdata prefix.

        OpenGL ReShade under Proton can be ignored when provided only through
        Steam launch options, so we write user.reg and track the backup in the
        install manifest for clean removal.
        """
        dll = Path(str(dll)).stem.lower()
        compat_paths = self._steam_compatdata_paths(appid)
        compat_path = compat_paths[0] if compat_paths else self._deck_expanduser(f"~/.local/share/Steam/steamapps/compatdata/{appid}")
        user_reg = Path(compat_path) / "pfx" / "user.reg"
        user_reg.parent.mkdir(parents=True, exist_ok=True)

        backup_path = ""
        text = "WINE REGISTRY Version 2\n\n"
        if user_reg.exists():
            text = user_reg.read_text(encoding="utf-8", errors="ignore")
            backup_path = str(user_reg.with_name(f"{user_reg.name}.decky-renodx-bak-{appid}-{int(time.time())}"))
            shutil.copy2(user_reg, backup_path)

        text = self._upsert_wine_dll_override_text(text, dll, value)
        user_reg.write_text(text, encoding="utf-8")
        if logger:
            logger.info("Set Wine DLL override for %s: %s=%s in %s", appid, dll, value, user_reg)
        return {
            "modified_files": [str(user_reg)],
            "backups": {str(user_reg): backup_path} if backup_path else {},
            "wine_dll_overrides": {dll: value},
        }

    def _upsert_wine_dll_override_text(self, text: str, dll: str, value: str) -> str:
        if not text.strip():
            text = "WINE REGISTRY Version 2\n\n"
        # Clean up the malformed single-backslash section that an older manual
        # experiment could leave behind, then write the canonical Wine section.
        text = re.sub(
            rf'(?ms)^\[Software\\Wine\\DllOverrides\].*?(?=^\[|\Z)',
            "",
            text,
        )
        section_re = re.compile(r'(?ms)^\[Software\\\\Wine\\\\DllOverrides\][^\n]*\n(?P<body>.*?)(?=^\[|\Z)')
        line_re = re.compile(rf'(?m)^"{re.escape(dll)}"="[^"]*"\n?')
        entry = f'"{dll}"="{value}"\n'
        match = section_re.search(text)
        if match:
            section = match.group(0)
            header = section.splitlines()[0] + "\n"
            body = section[len(header):]
            body = line_re.sub("", body).rstrip() + "\n" + entry
            replacement = header + body + "\n"
            return text[:match.start()] + replacement + text[match.end():]
        if not text.endswith("\n"):
            text += "\n"
        return text + f'\n[Software\\\\Wine\\\\DllOverrides] {int(time.time())}\n{entry}\n'

    def _cleanup_hdr_files_without_manifest(self, appid: str, exe_path: str = "", logger=None) -> dict:
        try:
            exe_dirs = self._hdr_cleanup_dirs(appid, exe_path)
            if not exe_dirs:
                return {"status": "noop", "message": "No game directory resolved for fallback cleanup."}
            candidates: list[Path] = []
            for exe_dir in exe_dirs:
                marker = self._read_game_hdr_marker(exe_dir)
                marker_dll = str(marker.get("dll", "")).strip()
                if marker_dll:
                    candidates.append(exe_dir / (marker_dll if marker_dll.endswith(".dll") else f"{marker_dll}.dll"))
                    candidates.append(exe_dir / f"{Path(marker_dll).stem}.ini")
                for name in [
                    "SpecialK.ini", "dxgi.ini", "d3d11.ini", "d3d12.ini", "d3d9.ini", "D3D9.ini", "dinput8.ini", "DINPUT8.ini", "ddraw.ini",
                    "ReShade.ini", "ReShadePreset.ini", "ReShade.log", "dxgi.log", "d3d11.log", "d3d12.log", "d3d9.log", "dinput8.log",
                    ".decky-renodx-hdr.json",
                    "AutoHDR32.addon", "AutoHDR64.addon", "AutoHDR.addon32", "AutoHDR.addon64",
                ]:
                    candidates.append(exe_dir / name)
                for pattern in ["*.addon", "*.addon32", "*.addon64", "renodx*.addon*", "lilium*.fx", "lilium*.fxh", "AdvancedAutoHDR.fx", "ConvertColorSpace.fx"]:
                    candidates.extend(exe_dir.glob(pattern))
                for dll in ["dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll", "D3D9.dll", "dinput8.dll", "DINPUT8.dll", "ddraw.dll", "DDraw.dll", "opengl32.dll"]:
                    ini = exe_dir / f"{Path(dll).stem}.ini"
                    path = exe_dir / dll
                    if ini.exists() or marker or (exe_dir / "ReShade.ini").exists() or any(exe_dir.glob("*.addon*")):
                        candidates.append(path)
                for pattern in ["reshade-shaders", "reshade-presets", "ReShade_shaders", "Shaders", "Textures"]:
                    path = exe_dir / pattern
                    if path.exists():
                        candidates.append(path)

            removed = []
            errors = []
            for path in sorted(set(candidates), key=lambda p: str(p)):
                if not path.exists():
                    continue
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    removed.append(str(path))
                    if logger:
                        logger.info(f"Fallback removal deleted {path}")
                except OSError as error:
                    errors.append(f"{path}: {error}")
                    if logger:
                        logger.error(f"Fallback removal failed for {path}: {error}")
            if errors:
                return {"status": "error", "message": "Some HDR files could not be removed.", "removed": removed, "errors": errors}
            if removed:
                return {"status": "success", "message": f"Removed {len(removed)} HDR component(s).", "removed": removed}
            return {"status": "noop", "message": "No HDR component files were found to remove.", "removed": []}
        except Exception as error:
            return {"status": "error", "message": f"Fallback cleanup failed: {error}"}

    def _hdr_cleanup_dirs(self, appid: str, exe_path: str = "") -> list[Path]:
        dirs: list[Path] = []
        def add(path: Path):
            if path.exists() and path.is_dir() and path not in dirs:
                dirs.append(path)
        if exe_path and os.path.exists(exe_path):
            add(Path(exe_path).parent)
        manifest = self.manifest_manager.read_manifest(appid)
        if manifest:
            for key in ["installed_files", "modified_files"]:
                for item in manifest.get(key, []) or []:
                    path = Path(item)
                    add(path if path.is_dir() else path.parent)
        try:
            base = Path(self._find_game_path(appid))
            add(base)
            for marker in base.rglob(".decky-renodx-hdr.json"):
                add(marker.parent)
            for ini in base.rglob("ReShade.ini"):
                add(ini.parent)
            for addon in base.rglob("*.addon*"):
                add(addon.parent)
            for ini in base.rglob("SpecialK.ini"):
                add(ini.parent)
        except Exception:
            pass
        return dirs

    async def verify_hdr_installation(self, appid: str, exe_path: str) -> dict:
        """Manually verify if the installed HDR method is actually working."""
        try:
            game_dir = os.path.dirname(exe_path)
            manifest = self.manifest_manager.read_manifest(appid)
            
            if not manifest:
                return {"status": "error", "message": "No installation manifest found for this game."}
            
            method = manifest.get("method")
            if method == "special_k":
                success, message = self.installer.verify_special_k(game_dir)
            elif method == "reshade":
                success, message = self.installer.verify_reshade(game_dir)
            else:
                return {"status": "success", "message": f"Verification not implemented for {method}."}
                
            if success:
                # Update manifest verified status
                manifest["verified"] = True
                self.manifest_manager.write_manifest(appid, manifest)
                return {"status": "success", "message": message}
            else:
                return {"status": "error", "message": message}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_game_hdr_status(self, appid: str, selected_executable_path: str = "") -> dict:
        try:
            primary_dir = self._resolve_game_exe_dir(appid, selected_executable_path)
            scan_dirs = self._hdr_status_dirs(appid, selected_executable_path, primary_dir)
            marker = {}
            files = {"renodx": [], "specialk": [], "reshade": [], "marker": []}
            detected_dir = primary_dir
            for exe_dir in scan_dirs:
                local_marker = self._read_game_hdr_marker(exe_dir)
                local_files = self._detect_game_hdr_files(exe_dir)
                if local_marker and not marker:
                    marker = local_marker
                for key in files:
                    files[key].extend([item for item in local_files.get(key, []) if item not in files[key]])
                if any(local_files.get(key) for key in ["renodx", "specialk", "reshade", "marker"]):
                    detected_dir = exe_dir
            launch_options = self._steam_launch_options(appid)
            launch_has_hdr = self._launch_options_have_hdr(launch_options)
            current_version = self._current_version()
            installed_version = str(marker.get("plugin_version", "")) if marker else ""
            installed = bool(files["renodx"] or files["specialk"] or files["reshade"] or launch_has_hdr)
            needs_update = bool(installed and installed_version and current_version and self._is_newer_version(installed_version, current_version))
            method = (
                "renodx" if files["renodx"]
                else "specialk" if files["specialk"]
                else "reshade-hdr" if files["reshade"]
                else str(marker.get("method", "")) if marker
                else "launch-options" if launch_has_hdr
                else ""
            )
            messages = []
            if installed:
                messages.append(f"HDR appears installed via {method or 'existing launch options'}.")
            else:
                messages.append("HDR injection is not detected for this game.")
            if installed and not launch_has_hdr:
                messages.append("HDR files are present, but HDR launch options are missing.")
            if needs_update:
                messages.append(f"Installed by plugin {installed_version}; current plugin is {current_version}. Refresh recommended.")
            return {
                "status": "success",
                "installed": installed,
                "method": method,
                "needs_update": needs_update,
                "plugin_version": current_version,
                "installed_version": installed_version,
                "launch_has_hdr": launch_has_hdr,
                "launch_options": launch_options,
                "exe_dir": str(detected_dir),
                "scan_dirs": [str(path) for path in scan_dirs],
                "files": files,
                "message": " ".join(messages),
            }
        except Exception as e:
            decky.logger.exception("Game HDR status check failed")
            return {"status": "error", "installed": False, "needs_update": False, "message": str(e)}

    def _hdr_status_dirs(self, appid: str, selected_executable_path: str, primary_dir: Path) -> list[Path]:
        dirs = [primary_dir]
        for path in self._hdr_cleanup_dirs(appid, selected_executable_path):
            if path not in dirs:
                dirs.append(path)
        return dirs

    async def repair_specialk_hdr_widget(self, appid: str, dll_override: str = "auto", selected_executable_path: str = "") -> dict:
        """Reset Special K UI/widget state and re-apply HDR defaults for the selected game."""
        try:
            exe_dir = self._resolve_game_exe_dir(appid, selected_executable_path)
            api = dll_override
            arch = "64"
            detected = await self._detect_api_for_path(str(exe_dir))
            if detected.get("status") == "success":
                arch = str(detected.get("architecture") or "64")
                if api == "auto":
                    api = str(detected.get("api") or "dxgi")
            elif api == "auto":
                api = "dxgi"

            exe_dir = self._compat_specialk_install_dir(appid, exe_dir)
            dll = self._specialk_dll_for_game(appid, {"injection_dll": api}, api)
            result = self._install_specialk_for_game(exe_dir, dll, arch, appid)
            if result.get("status") != "success":
                return result

            backed_up = self._reset_specialk_imgui_state(exe_dir)
            for ini in [exe_dir / "SpecialK.ini", exe_dir / f"{dll}.ini"]:
                self._write_specialk_hdr_ini(ini, repair_widget=True)
            self._fix_deck_user_ownership(exe_dir)
            return {
                "status": "success",
                "method": "specialk-widget-repair",
                "output": (
                    f"Repaired Special K HDR widget state for {dll.upper()} ({arch}-bit).\n"
                    f"Backed up {backed_up} Special K UI state file(s). Relaunch the game, open the main Special K overlay, then try HDR Setup again."
                ),
            }
        except Exception as e:
            decky.logger.exception("Special K HDR widget repair failed")
            return {"status": "error", "message": str(e)}

    def _resolve_game_exe_dir(self, appid: str, selected_executable_path: str = "") -> Path:
        if selected_executable_path and os.path.exists(selected_executable_path):
            return Path(selected_executable_path).parent
        return Path(self._find_game_path(appid))

    def _write_game_hdr_marker(self, exe_dir: Path, appid: str, method: str, dll: str, arch: str, extra: dict[str, Any] | None = None) -> None:
        marker = {
            "appid": appid,
            "method": method,
            "dll": dll,
            "arch": arch,
            "plugin_version": self._current_version(),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            marker.update(extra)
        (exe_dir / ".decky-renodx-hdr.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")

    def _read_game_hdr_marker(self, exe_dir: Path) -> dict[str, Any]:
        marker_path = exe_dir / ".decky-renodx-hdr.json"
        if not marker_path.exists():
            return {}
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _remove_game_hdr_marker(self, exe_dir: Path) -> None:
        try:
            marker = exe_dir / ".decky-renodx-hdr.json"
            if marker.exists():
                marker.unlink()
        except OSError as error:
            decky.logger.warning("Could not remove game HDR marker for %s: %s", exe_dir, error)

    def _detect_game_hdr_files(self, exe_dir: Path) -> dict[str, Any]:
        renodx_files = sorted(path.name for path in exe_dir.glob("*.addon*") if "renodx" in path.name.lower())
        specialk_files = sorted(path.name for path in exe_dir.glob("*.dll") if (exe_dir / f"{path.stem}.ini").exists() and "reshade" not in path.name.lower())
        reshade_files = sorted(path.name for path in exe_dir.glob("ReShade.ini"))
        return {
            "renodx": renodx_files,
            "specialk": specialk_files,
            "reshade": reshade_files,
            "marker": [".decky-renodx-hdr.json"] if (exe_dir / ".decky-renodx-hdr.json").exists() else [],
        }

    def _steam_launch_options(self, appid: str) -> str:
        for localconfig in self._steam_localconfig_candidates():
            try:
                text = localconfig.read_text(encoding="utf-8", errors="ignore")
                needle = f'"{appid}"'
                pos = 0
                while True:
                    idx = text.find(needle, pos)
                    if idx < 0:
                        break
                    brace = text.find("{", idx)
                    if brace < 0:
                        break
                    depth = 0
                    end = None
                    for offset in range(brace, len(text)):
                        if text[offset] == "{":
                            depth += 1
                        elif text[offset] == "}":
                            depth -= 1
                            if depth == 0:
                                end = offset + 1
                                break
                    if end is None:
                        break
                    block = text[idx:end]
                    match = re.search(r'"LaunchOptions"\s+"((?:\\.|[^"\\])*)"', block)
                    if match:
                        return bytes(match.group(1), "utf-8").decode("unicode_escape")
                    pos = idx + 1
            except Exception:
                continue
        return ""

    def _launch_options_have_hdr(self, launch_options: str) -> bool:
        return any(token in launch_options for token in ["PROTON_ENABLE_HDR=1", "DXVK_HDR=1", "ENABLE_HDR_WSI=1", "ENABLE_GAMESCOPE_WSI=1", "WINEDLLOVERRIDES="])

    async def _detect_api_for_path(self, path: str) -> dict:
        try:
            game_path = Path(path)
            search_root = game_path if game_path.is_dir() else game_path.parent
            has_hdr_install_marker = (search_root / ".decky-renodx-hdr.json").exists() or (search_root / "ReShade.ini").exists()
            script_result = self._detect_api_with_letmereshade_script(game_path)
            if script_result.get("status") == "success" and script_result.get("api") != "dxgi" and not has_hdr_install_marker:
                return self._apply_engine_api_hints(game_path, script_result)

            detected_api = "unknown"
            arch = str(script_result.get("architecture", "64")) if script_result.get("status") == "success" else "64"
            exe_files = []
            for exe in search_root.rglob("*.exe"):
                name = exe.name.lower()
                if any(skip in name for skip in ["unins", "launcher", "crash", "setup", "config", "redist"]):
                    continue
                try:
                    exe_files.append((exe, exe.stat().st_size))
                except OSError:
                    continue
            exe_files.sort(key=lambda item: item[1], reverse=True)

            for exe, _size in exe_files[:3]:
                try:
                    pe_arch = pe_architecture(exe)
                    if pe_arch in {"32", "64"}:
                        arch = pe_arch
                    result = subprocess.run(["file", str(exe)], capture_output=True, text=True, env=self._clean_subprocess_env(), timeout=10)
                    if pe_arch == "unknown" and "PE32 executable" in result.stdout and "PE32+" not in result.stdout:
                        arch = "32"
                    elif pe_arch == "unknown" and ("PE32+ executable" in result.stdout or "x86-64" in result.stdout):
                        arch = "64"
                except Exception:
                    pass

                detected_api = self._detect_api_from_binary_imports(exe)
                if detected_api == "d3d9" and arch == "64" and script_result.get("api") == "dxgi":
                    detected_api = "dxgi"
                if detected_api != "unknown":
                    result = {"status": "success", "api": detected_api, "architecture": arch, "injection_dll": self._api_to_injection_dll(detected_api)}
                    if script_result.get("status") == "success":
                        result["detector"] = "python-with-letmereshade-arch"
                        result["script_api_hint"] = script_result.get("api")
                    return self._apply_engine_api_hints(game_path, result)

            dll_candidates = []
            for pattern in ["*.dll", "*.DLL"]:
                dll_candidates.extend(search_root.glob(pattern))
            hdr_proxy_names = {"dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll", "d3d8.dll", "ddraw.dll", "dinput8.dll", "opengl32.dll"}
            priority_names = ["unityplayer.dll", "gameassembly.dll", "mono.dll", "mono-2.0-bdwgc.dll"]
            dll_candidates.sort(key=lambda item: (0 if item.name.lower() in priority_names else 1, item.name.lower()))
            for dll in dll_candidates[:12]:
                if has_hdr_install_marker and dll.name.lower() in hdr_proxy_names:
                    continue
                detected_api = self._detect_api_from_binary_imports(dll)
                if detected_api != "unknown":
                    result = {"status": "success", "api": detected_api, "architecture": arch, "injection_dll": self._api_to_injection_dll(detected_api)}
                    if script_result.get("status") == "success":
                        result["detector"] = "python-with-letmereshade-arch"
                        result["script_api_hint"] = script_result.get("api")
                    return self._apply_engine_api_hints(game_path, result)

            if any((search_root / name).exists() for name in ["UnityPlayer.dll", "GameAssembly.dll"]):
                return {"status": "success", "api": "dx11_dx12", "architecture": arch, "injection_dll": "dxgi", "engine": "unity", "confidence": "heuristic", "notes": "Unity runtime detected; treating API as DX11/DX12 family."}

            if detected_api == "unknown" and script_result.get("status") == "success" and script_result.get("api") == "dxgi":
                detected_api = "dxgi"
            elif arch == "32" and detected_api == "unknown":
                detected_api = "d3d9"
            result = {"status": "success", "api": detected_api, "architecture": arch, "injection_dll": self._api_to_injection_dll(detected_api)}
            if script_result.get("status") == "success":
                result["detector"] = "python-with-letmereshade-arch"
                result["script_api_hint"] = script_result.get("api")
                if detected_api == "dxgi":
                    result["confidence"] = "hook-default"
                    result["notes"] = "LetMeReShade detected a 64-bit Windows executable and selected DXGI as the DirectX 10/11/12 hook."
            return self._apply_engine_api_hints(game_path, result)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _apply_engine_api_hints(self, path: Path, result: dict[str, Any]) -> dict[str, Any]:
        engine = self._detect_engine_family(path)
        if engine != "unknown":
            result = dict(result)
            result["engine"] = engine
        if engine == "unreal" and result.get("architecture") == "64" and result.get("api") in ["unknown", "dxgi"]:
            result.update({
                "api": "dx11_dx12",
                "injection_dll": "dxgi",
                "confidence": "heuristic",
                "notes": "Unreal Engine Win64/Shipping layout detected; treating API as DX11/DX12 selectable and using DXGI injection.",
            })
        elif engine == "unity" and result.get("architecture") == "64" and result.get("api") in ["unknown", "dxgi", "d3d11", "opengl32"]:
            result.update({
                "api": "dx11_dx12",
                "injection_dll": "dxgi",
                "confidence": "heuristic",
                "notes": "Unity runtime detected; treating API as DX11/DX12 family and using DXGI injection.",
            })
        elif "injection_dll" not in result:
            result["injection_dll"] = self._api_to_injection_dll(str(result.get("api", "unknown")))
        return result

    def _detect_engine_family(self, search_root: Path) -> str:
        roots = self._engine_scan_roots(search_root)
        for root in roots:
            root_parts = [part.lower() for part in root.parts]
            if (
                any(path.is_file() for path in root.glob("*.uproject"))
                or any(path.is_file() and "shipping" in path.name.lower() for path in root.glob("*.exe"))
                or any(path.is_file() and "shipping" in path.name.lower() for path in root.glob("*/*/Binaries/Win64/*.exe"))
                or any(path.is_file() and "shipping" in path.name.lower() for path in root.glob("*/Binaries/Win64/*.exe"))
                or ("binaries" in root_parts and "win64" in root_parts and any(path.is_file() and "shipping" in path.name.lower() for path in root.glob("*.exe")))
            ):
                return "unreal"
            if (
                (root / "UnityPlayer.dll").exists()
                or (root / "GameAssembly.dll").exists()
                or any(path.is_file() and path.name.lower() == "globalgamemanagers" for path in root.glob("*_Data/globalgamemanagers"))
                or any(path.is_file() and path.name.lower() in {"unityplayer.dll", "gameassembly.dll"} for path in root.glob("*/*.dll"))
                or any(path.is_file() and path.name.lower() == "globalgamemanagers" for path in root.glob("*/*_Data/globalgamemanagers"))
            ):
                return "unity"
        return "unknown"

    def _engine_scan_roots(self, path: Path) -> list[Path]:
        start = path if path.is_dir() else path.parent
        roots = []
        current = start
        for _ in range(5):
            if current.name.lower() in {"common", "steamapps"}:
                break
            if current not in roots and current.exists():
                roots.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
        return roots

    def _api_to_injection_dll(self, api: str) -> str:
        if api in ["dx11_dx12", "dx10", "dx11", "dx12", "d3d11", "d3d12", "dxgi"]:
            return "dxgi"
        return api if api in ["d3d9", "d3d8", "opengl32", "ddraw", "dinput8"] else "dxgi"

    def _detect_api_with_letmereshade_script(self, path: Path) -> dict[str, Any]:
        game_dir = path if path.is_dir() else path.parent
        script_path = self._get_assets_dir() / "reshade-game-manager.sh"
        if not script_path.exists() or not game_dir.exists():
            return {"status": "error", "message": "LetMeReShade detector unavailable."}
        try:
            clean_env = {**os.environ, **self.environment}
            clean_env["LD_LIBRARY_PATH"] = ""
            result = subprocess.run(
                ["/bin/bash", str(script_path), "detect", str(game_dir), "auto"],
                cwd=str(script_path.parent),
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"status": "error", "message": str(error)}
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip() or result.stdout.strip()}

        matches = re.findall(r"(?m)^(32|64),(d3d8|d3d9|d3d11|d3d12|dxgi|opengl32|ddraw|dinput8)\s*$", result.stdout)
        if not matches:
            return {"status": "error", "message": f"Could not parse LetMeReShade detector output: {result.stdout[-200:]}"}
        arch, api = matches[-1]
        return {
            "status": "success",
            "api": api,
            "architecture": arch,
            "injection_dll": self._api_to_injection_dll(api),
            "detector": "letmereshade",
            "stdout": result.stdout[-1000:],
        }

    def _detect_api_from_binary_imports(self, binary_path: Path) -> str:
        try:
            dlls = imported_dlls(binary_path)
            for api in ["d3d12", "d3d11", "dxgi", "d3d9", "d3d8", "ddraw", "dinput8", "opengl32"]:
                if f"{api}.dll" in dlls:
                    return api
        except Exception:
            pass

        imports = ""
        try:
            result = subprocess.run(["objdump", "-p", str(binary_path)], capture_output=True, text=True, env=self._clean_subprocess_env(), timeout=15)
            imports = result.stdout.lower()
        except Exception:
            pass
        if not imports:
            try:
                imports = binary_path.read_bytes().decode("latin-1", errors="ignore").lower()
            except OSError:
                imports = ""
        if re.search(r"\bopengl32(?:\.dll)?\b|\bwgl(?:createcontext|deletecontext|getprocaddress|makecurrent|swapbuffers|choosepixelformat|setpixelformat)\b", imports):
            return "opengl32"
        for api in ["d3d12", "d3d11", "dxgi", "d3d9", "d3d8", "ddraw", "dinput8"]:
            if f"{api}.dll" in imports or f"{api.lower()}.dll" in imports:
                return api
        return "unknown"

    def _normalize_title(self, title: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()

    def _install_specialk_for_game(self, exe_dir: Path, dll_override: str, arch: str, appid: str = "") -> dict:
        specialk_dir = Path(self.main_path) / "SpecialK"
        if not specialk_dir.exists():
            return {"status": "error", "message": "Special K runtime is not installed."}
        source_name = "SpecialK32.dll" if arch == "32" else "SpecialK64.dll"
        candidates = list(specialk_dir.rglob(source_name))
        if not candidates:
            return {"status": "error", "message": f"{source_name} was not found in the Special K runtime."}
        dll = self._specialk_hook_dll(dll_override)
        target = exe_dir / f"{dll}.dll"
        shutil.copy2(candidates[0], target)
        target.chmod(0o666)
        ini = exe_dir / "SpecialK.ini"
        self._write_specialk_hdr_ini(ini, appid=appid)
        self._write_specialk_hdr_ini(exe_dir / f"{dll}.ini", appid=appid)
        return {"status": "success", "dll": dll}

    def _write_specialk_hdr_ini(self, ini: Path, repair_widget: bool = False, appid: str = "") -> None:
        text = ini.read_text(encoding="utf-8", errors="ignore") if ini.exists() else ""
        updates = {
            "SpecialK.System": {
                "UsingWINE": "true",
            },
            "Render.OSD": {
                "HDRLuminance": "9.375",
            },
            "SpecialK.HDR": {
                "HDR.Enable": "true",
                "Use16BitSwapChain": "true",
                "AllowFullLuminance": "true",
                "scRGBLuminance_[0]": "18.75",
                "scRGBGamma_[0]": "1.0",
                "ToneMapper_[0]": "1",
                "Saturation_[0]": "1.0",
                "MiddleGray_[0]": "1.25",
                "Preset": "0",
            },
        }
        if repair_widget:
            updates["Render.FrameRate"] = {
                "SleeplessRenderThread": "false",
                "SleeplessWindowThread": "false",
            }
            updates["ImGui.Render"] = {
                "Scale": "1.0",
                "UseHardwareCursor": "false",
            }
            
        if hasattr(self, "compat_db") and str(appid) in self.compat_db.get("games", {}):
            game_compat = self.compat_db["games"][str(appid)]
            sk_compat = game_compat.get("tools", {}).get("special_k", {})
            
            # Apply ini tweaks
            tweaks = sk_compat.get("special_k_ini_tweaks", {})
            for section, values in tweaks.items():
                if section not in updates:
                    updates[section] = {}
                updates[section].update(values)
                decky.logger.info(f"Applying Special K compatibility INI tweak to {appid}: [{section}] {values}")
                
            # Apply delay
            delay = sk_compat.get("special_k_delay_seconds", 0)
            if delay > 0:
                if "SpecialK.System" not in updates:
                    updates["SpecialK.System"] = {}
                updates["SpecialK.System"]["GlobalInjectDelay"] = str(float(delay))
                decky.logger.info(f"Applying Special K injection delay to {appid}: {delay}s")

        for section, values in updates.items():
            text = self._upsert_ini_section_values(text, section, values)
        ini.write_text(text, encoding="utf-8")
        ini.chmod(0o666)

    def _reset_specialk_imgui_state(self, exe_dir: Path) -> int:
        backed_up = 0
        patterns = [
            "imgui.ini",
            "imgui*.ini",
            "SpecialK*.log",
            "logs/SpecialK*.log",
            "logs/imgui*.ini",
        ]
        timestamp = int(time.time())
        for pattern in patterns:
            for path in exe_dir.glob(pattern):
                if not path.is_file():
                    continue
                backup = path.with_name(f"{path.name}.decky-renodx-backup-{timestamp}")
                try:
                    path.rename(backup)
                    backed_up += 1
                except OSError as error:
                    decky.logger.warning("Could not back up Special K UI state file %s: %s", path, error)
        return backed_up

    def _upsert_ini_section_values(self, text: str, section: str, values: dict[str, str]) -> str:
        if not text.strip():
            text = ""
        section_pattern = rf"(?ims)^\[{re.escape(section)}\]\s*(.*?)(?=^\[[^\]]+\]|\Z)"
        match = re.search(section_pattern, text)
        body = match.group(1) if match else ""
        for key, value in values.items():
            line = f"{key}={value}"
            if re.search(rf"(?im)^{re.escape(key)}\s*=", body):
                body = re.sub(rf"(?im)^{re.escape(key)}\s*=.*$", lambda _match, line=line: line, body)
            else:
                body = body.rstrip() + ("\n" if body.strip() else "") + line + "\n"
        replacement = f"[{section}]\n{body.strip()}\n\n"
        if match:
            return text[:match.start()] + replacement + text[match.end():]
        return text.rstrip() + ("\n\n" if text.strip() else "") + replacement

    def _specialk_hook_dll(self, dll_override: str) -> str:
        dll = (dll_override or "dxgi").lower()
        if dll.endswith(".dll"):
            dll = dll[:-4]
        if dll == "auto":
            return "dxgi"
        if dll in {"dxgi", "d3d11", "d3d9", "d3d8", "opengl32", "dinput8", "ddraw"}:
            return dll
        return "dxgi"

    def _compat_specialk_tool(self, appid: str) -> dict[str, Any]:
        if not hasattr(self, "compat_db"):
            return {}
        game_compat = self.compat_db.get("games", {}).get(str(appid), {})
        special_k = game_compat.get("tools", {}).get("special_k", {})
        return special_k if isinstance(special_k, dict) else {}

    def _compat_tool_metadata(self, method: str, tool_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(tool_data, dict):
            return {}
        automation = tool_data.get("automation", {}) if isinstance(tool_data.get("automation", {}), dict) else {}
        warnings = []
        manual_steps = []
        for source, target in [
            (automation.get("warnings"), warnings),
            (tool_data.get("warnings"), warnings),
            (automation.get("manual_steps"), manual_steps),
            (tool_data.get("manual_steps"), manual_steps),
        ]:
            if isinstance(source, list):
                target.extend(str(item) for item in source if str(item).strip())
            elif isinstance(source, str) and source.strip():
                target.append(source.strip())
        metadata: dict[str, Any] = {}
        if warnings:
            metadata["warnings"] = list(dict.fromkeys(warnings))
        if manual_steps:
            metadata["manual_steps"] = list(dict.fromkeys(manual_steps))
        if method == "special_k":
            preferred = automation.get("preferred_injection")
            if preferred:
                metadata["preferred_injection"] = str(preferred)
            if automation.get("hdr", {}).get("avoid") if isinstance(automation.get("hdr"), dict) else False:
                metadata.setdefault("warnings", []).append("Compatibility database marks Special K HDR as avoid for this game.")
        return metadata

    def _compat_specialk_hook_dll(self, appid: str) -> str:
        special_k = self._compat_specialk_tool(appid)
        if not special_k:
            return ""

        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        local_dll = automation.get("local_dll", {}) if isinstance(automation.get("local_dll", {}), dict) else {}
        candidates = [
            local_dll.get("target"),
            automation.get("addon_loader", {}).get("special_k_target") if isinstance(automation.get("addon_loader"), dict) else "",
            automation.get("special_k_target"),
            automation.get("target_dll"),
            automation.get("injection_dll"),
            special_k.get("special_k_target"),
            special_k.get("target_dll"),
            special_k.get("injection_dll"),
        ]
        for candidate in candidates:
            if candidate:
                return self._specialk_hook_dll(Path(str(candidate)).stem)

        tweaks = special_k.get("special_k_ini_tweaks", {})
        if isinstance(tweaks, dict):
            for section in tweaks:
                section_l = str(section).lower()
                if "d3d9" in section_l:
                    return "d3d9"
                if "d3d11" in section_l:
                    return "d3d11"
                if "dxgi" in section_l:
                    return "dxgi"

        notes = " ".join(str(item) for item in [special_k.get("notes", ""), *automation.get("warnings", [])]).lower()
        if "d3d9" in notes:
            return "d3d9"
        if "d3d11" in notes:
            return "d3d11"
        return ""

    def _compat_specialk_install_dir(self, appid: str, exe_dir: Path) -> Path:
        special_k = self._compat_specialk_tool(appid)
        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        local_dll = automation.get("local_dll", {}) if isinstance(automation.get("local_dll", {}), dict) else {}
        relative = str(local_dll.get("relative_path") or "").strip()
        if not relative or relative.lower() in {"<path-to-game>", ".", "./"}:
            return exe_dir
        relative = relative.replace("\\", "/").strip("/")
        target = (exe_dir / relative).resolve()
        try:
            if not target.is_relative_to(exe_dir.resolve()):
                return exe_dir
        except OSError:
            return exe_dir
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _compat_specialk_force_render_api(self, appid: str) -> str:
        special_k = self._compat_specialk_tool(appid)
        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        forced = str(automation.get("force_render_api") or special_k.get("force_render_api") or "").lower()
        if not forced:
            return ""
        if "12" in forced:
            return "dx12"
        if "11" in forced:
            return "dx11"
        if "10" in forced:
            return "dx10"
        if "9" in forced:
            return "dx9"
        if "vulkan" in forced:
            return "vulkan"
        if "opengl" in forced:
            return "opengl"
        return ""

    def _compat_specialk_avoid_hdr(self, appid: str) -> bool:
        special_k = self._compat_specialk_tool(appid)
        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        hdr = automation.get("hdr", {}) if isinstance(automation.get("hdr"), dict) else {}
        return bool(hdr.get("avoid"))

    def _compat_specialk_delay(self, appid: str) -> int:
        special_k = self._compat_specialk_tool(appid)
        try:
            return max(1, int(float(special_k.get("special_k_delay_seconds", 5) or 5)))
        except (TypeError, ValueError):
            return 5

    def _specialk_global_delay_gate(self, appid: str) -> dict[str, Any]:
        special_k = self._compat_specialk_tool(appid)
        if not special_k:
            return {"available": False, "reason": "No compatibility entry requires delayed/global Special K injection."}
        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        preferred = str(automation.get("preferred_injection") or "").lower()
        hdr = automation.get("hdr", {}) if isinstance(automation.get("hdr"), dict) else {}
        if hdr.get("avoid"):
            return {"available": False, "reason": "Compatibility database says to avoid Special K HDR for this game."}
        if automation.get("anti_cheat"):
            return {"available": False, "reason": "Requires anti-cheat/online-service changes before global injection can be considered safe."}
        if "global" in preferred and ("delayed" in preferred or automation.get("avoid_injection_at_launch")):
            return {"available": True, "reason": f"Experimental: compatibility data requests {preferred.replace('_', ' ')} injection with a {self._compat_specialk_delay(appid)}s delay."}
        return {"available": False, "reason": "This entry does not require delayed/global Special K injection."}

    def _upsert_specialk_global_profile(self, ini: Path, title: str, exe_path: str, delay: int) -> None:
        text = ini.read_text(encoding="utf-8", errors="ignore") if ini.exists() else ""
        section = f"Profile.{Path(exe_path).stem}"
        text = self._upsert_ini_section_values(text, section, {
            "Title": title,
            "Executable": exe_path,
            "Enabled": "true",
            "GlobalInjectDelay": str(float(delay)),
        })
        ini.write_text(text, encoding="utf-8")
        ini.chmod(0o666)

    def _specialk_local_install_gate(self, appid: str) -> dict[str, Any]:
        special_k = self._compat_specialk_tool(appid)
        if not special_k:
            return {"available": True, "reason": "No compatibility block."}
        automation = special_k.get("automation", {}) if isinstance(special_k.get("automation", {}), dict) else {}
        preferred = str(automation.get("preferred_injection") or "").lower()
        local_dll = automation.get("local_dll", {}) if isinstance(automation.get("local_dll", {}), dict) else {}
        avoid_modes = [str(item).lower() for item in automation.get("avoid_injection_modes", [])] if isinstance(automation.get("avoid_injection_modes"), list) else []
        hdr = automation.get("hdr", {}) if isinstance(automation.get("hdr"), dict) else {}

        if hdr.get("avoid"):
            return {"available": False, "reason": "Compatibility database says to avoid Special K HDR for this game."}
        if automation.get("hardware_requirement") and str(automation.get("hardware_requirement")).lower() != "steam deck":
            return {"available": False, "reason": f"Special K compatibility requires {automation.get('hardware_requirement')}, not Steam Deck OLED."}
        if automation.get("required_wrapper"):
            return {"available": False, "reason": f"Requires unsupported wrapper: {', '.join(map(str, automation.get('required_wrapper', [])))}."}
        if automation.get("required_files"):
            files = ", ".join(str(item.get("file", item)) for item in automation.get("required_files", []))
            return {"available": False, "reason": f"Requires extra Special K file(s) not installed by this plugin: {files}."}
        if "local" in avoid_modes:
            return {"available": False, "reason": "Compatibility database says local Special K injection should be avoided."}
        if automation.get("avoid_injection_at_launch") and not local_dll:
            return {"available": False, "reason": "Requires delayed/global injection; this plugin currently only supports local DLL injection."}
        if preferred.startswith("global") and "local" not in preferred and not local_dll:
            return {"available": False, "reason": f"Requires {preferred.replace('_', ' ')} injection; local DLL injection is not safe for this entry."}
        if automation.get("anti_cheat"):
            return {"available": False, "reason": "Requires anti-cheat/online-service changes before Special K can be considered safe."}
        return {"available": True, "reason": "Local Special K install is allowed by compatibility data."}

    def _hdr_method_options(self, appid: str, context: dict[str, Any], recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rec_methods = {str(rec.get("method")): rec for rec in recommendations}
        anti_cheat = bool(context.get("anti_cheat")) or bool(context.get("is_multiplayer"))
        renodx_match = context.get("renodx_match") or {}
        engine_bucket = self._renodx_engine_bucket(str(context.get("engine") or ""))
        architecture = str(context.get("architecture") or "unknown")
        generic_renodx_tryable = engine_bucket in {"unity", "unreal"} and architecture == "64"
        renodx_available = bool((context.get("renodx_supported") and renodx_match) or generic_renodx_tryable)
        renodx_reason = "No RenoDX/Luma match found."
        renodx_badge = ""
        if renodx_match:
            match_type = str(renodx_match.get("match_type") or "")
            if match_type == "generic_engine" and architecture != "64":
                renodx_available = False
                renodx_reason = f"Generic RenoDX requires a confirmed 64-bit game. Detected architecture: {architecture}."
                renodx_badge = ""
            else:
                renodx_reason = f"Match found: {renodx_match.get('name', 'RenoDX')}."
                renodx_badge = "Experimental" if match_type == "generic_engine" else "Best"
        elif generic_renodx_tryable:
            renodx_reason = f"No exact RenoDX match, but generic {engine_bucket.title()} RenoDX may work. This is experimental."
            renodx_badge = "Experimental"
        elif engine_bucket in {"unity", "unreal"}:
            renodx_reason = f"Generic {engine_bucket.title()} RenoDX requires confirmed 64-bit architecture. Detected: {architecture}."
        specialk_gate = self._specialk_local_install_gate(appid)
        delayed_gate = self._specialk_global_delay_gate(appid)
        reshade_api = str(context.get("injection_dll") or "auto")
        if reshade_api in {"", "auto"}:
            reshade_api = "automatic"

        def option(method: str, label: str, available: bool, reason: str, badge: str = "") -> dict[str, Any]:
            rec = rec_methods.get(method, {})
            return {
                "method": method,
                "label": label,
                "available": bool(available),
                "reason": reason,
                "badge": badge,
                "score": rec.get("score"),
                "confidence": rec.get("confidence", "medium"),
            }

        return [
            option("recommended", "Recommended", bool(recommendations and recommendations[0].get("score", 0) > 0), recommendations[0].get("reason", "No safe recommendation.") if recommendations else "No recommendation."),
            option("renodx", "RenoDX / Luma", renodx_available and not anti_cheat, renodx_reason, renodx_badge),
            option("special_k", "Special K", specialk_gate["available"] and not anti_cheat, specialk_gate["reason"], "Verified" if context.get("has_special_k_compat") else ""),
            option("special_k_delayed", "Special K Delayed", delayed_gate["available"] and not anti_cheat, delayed_gate["reason"], "Experimental" if delayed_gate["available"] else ""),
            option("reshade", "ReShade AutoHDR", not anti_cheat, f"Fallback injection using {reshade_api}.", "Fallback"),
            option("native_hdr", "Native HDR / No Injection", True, f"Native HDR status: {context.get('native_hdr', 'unknown')}."),
            option("sdr", "SDR / Remove Injection", True, "Remove injected HDR files and launch options."),
        ]

    def _specialk_dll_for_game(self, appid: str, context: dict[str, Any] | None = None, fallback: str = "dxgi") -> str:
        context = context or {}
        compat_dll = self._compat_specialk_hook_dll(appid)
        if compat_dll:
            return compat_dll
        context_dll = str(context.get("special_k_injection_dll") or context.get("injection_dll") or "")
        return self._specialk_hook_dll(context_dll or fallback)

    def _parse_reshade_selected_api(self, output: str) -> str:
        match = re.search(r"Selected API:\s*([a-z0-9_]+)", output or "", re.I)
        if match:
            return self._api_to_injection_dll(match.group(1).lower())
        matches = re.findall(r"([a-z0-9_]+)=n,b", output or "", re.I)
        for item in reversed(matches):
            if item.lower() != "d3dcompiler_47":
                return self._api_to_injection_dll(item.lower())
        return ""

    def _hdr_launch_options(self, dll: str, appid: str = "", active_tool: str = "") -> str:
        overrides = []
        for item in str(dll or "dxgi").split(";"):
            item = item.strip()
            if item:
                overrides.append(f"{item}=n,b")

        env = "PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1"

        if not any(item.startswith("opengl32=") for item in overrides):
            overrides.insert(0, "d3dcompiler_47=n")

        dll_overrides = f'WINEDLLOVERRIDES="{";".join(overrides)}"'

        compat_flags = ""
        if hasattr(self, "compat_db") and str(appid) in self.compat_db.get("games", {}):
            game_compat = self.compat_db["games"][str(appid)]
            tool_compat = game_compat.get("tools", {}).get(active_tool, {})
            if "launch_options" in tool_compat:
                compat_flags = " " + " ".join(tool_compat["launch_options"])
                decky.logger.info(f"Applying compatibility launch options for {appid} ({active_tool}): {compat_flags}")

        if overrides == ["opengl32=n,b"]:
            return f"{env} %command%{compat_flags}"

        return f"{env} {dll_overrides} %command%{compat_flags}"

    async def list_installed_games(self) -> dict:
        try:
            games = []
            seen_appids = set()

            library_file = self._find_libraryfolders_file()
            if library_file:
                library_paths = self._steam_library_paths(library_file)
                for library_path in library_paths:
                    steamapps_path = Path(library_path) / "steamapps"
                    if not steamapps_path.exists():
                        continue
                    for appmanifest in steamapps_path.glob("appmanifest_*.acf"):
                        game_info = self._read_appmanifest(appmanifest)
                        appid = game_info.get("appid")
                        name = game_info.get("name")
                        if appid and name and appid not in seen_appids:
                            # Filter system components
                            if not any(exclude in name for exclude in ["Proton", "Steam Linux Runtime", "Steamworks Common Redistributables"]):
                                seen_appids.add(appid)
                                games.append({"appid": appid, "name": name, "source": "steam"})

            games.sort(key=lambda game: game["name"].lower())
            return {"status": "success", "games": games}
        except Exception as e:
            decky.logger.error(str(e))
            return {"status": "error", "games": [], "message": str(e)}

    def _steam_root_candidates(self) -> list[Path]:
        home = self._deck_user_home()
        return [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]

    def _find_libraryfolders_file(self) -> Path | None:
        for steam_root in self._steam_root_candidates():
            candidate = steam_root / "steamapps" / "libraryfolders.vdf"
            if candidate.exists():
                return candidate
        return None

    def _steam_library_paths(self, library_file: Path) -> list[str]:
        paths = [str(library_file.parents[1])]
        text = library_file.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'"path"\s+"((?:\\.|[^"\\])*)"', text):
            path = bytes(match.group(1), "utf-8").decode("unicode_escape").replace("\\\\", "/")
            if path not in paths:
                paths.append(path)
        return paths

    def _read_appmanifest(self, manifest_path: Path) -> dict[str, str]:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        result = {}
        for key in ["appid", "name", "installdir"]:
            match = re.search(rf'"{key}"\s+"((?:\\.|[^"\\])*)"', text)
            if match:
                result[key] = bytes(match.group(1), "utf-8").decode("unicode_escape")
        return result

    def _find_game_executable_directory(self, path: Path, game_name: str) -> tuple[Path, float]:
        """
        Unified function to find the game executable directory with smart detection
        
        Args:
            path: Base path to search for game executables
            game_name: Name of the game for matching
            
        Returns:
            tuple[Path, float]: The best executable directory and its score
        """
        try:
            if not path.exists() or not path.is_dir():
                return path, 0
                
            # Extract words from game name for better matching
            game_words = set(re.findall(r'\w+', game_name.lower()))
            # Clean game name (remove spaces, special chars)
            clean_game_name = re.sub(r'[^a-z0-9]', '', game_name.lower())
            
            decky.logger.info(f"Looking for executables for game: {game_name}")
            decky.logger.info(f"Game words for matching: {game_words}")
            
            def analyze_directory_content(dir_path: Path) -> float:
                """Score a directory based on its content"""
                if not dir_path.exists() or not dir_path.is_dir():
                    return 0
                    
                score = 0
                file_types = {'exe': 0, 'dll': 0, 'config': 0, 'asset': 0, 'setup': 0, 'redist': 0}
                
                try:
                    # Count file types
                    for file in dir_path.iterdir():
                        if file.is_file():
                            ext = file.suffix.lower()
                            
                            # Game binary files
                            if ext == '.exe':
                                file_types['exe'] += 1
                            elif ext == '.dll':
                                file_types['dll'] += 1
                                
                            # Game config and data files
                            elif ext in ['.ini', '.cfg', '.xml', '.json', '.txt']:
                                file_types['config'] += 1
                                
                            # Game asset files
                            elif ext in ['.pak', '.dat', '.bsa', '.ba2', '.dds', '.tga', '.png', '.jpg']:
                                file_types['asset'] += 1
                                
                            # Setup and redistributable files (negative indicators)
                            elif ext in ['.msi', '.cab', '.msm']:
                                file_types['setup'] += 1
                            
                            # Check file names for redistributable indicators
                            file_name = file.name.lower()
                            if any(term in file_name for term in ['redist', 'vcredist', 'directx', 'setup', 'install']):
                                file_types['redist'] += 1
                    
                    # Score based on file types
                    # Game directories usually have more DLLs and game-related files
                    score += file_types['dll'] * 0.5  # DLLs are good indicators
                    score += file_types['config'] * 0.3  # Config files are somewhat good indicators
                    score += file_types['asset'] * 0.4  # Asset files are good indicators
                    
                    # Too many EXEs might indicate a utility directory
                    if file_types['exe'] > 5:
                        score -= (file_types['exe'] - 5) * 0.2
                    
                    # Setup files are negative indicators
                    score -= file_types['setup'] * 1.0
                    score -= file_types['redist'] * 1.0
                    
                    # Check directory name - look for similarity to game name
                    dir_name = dir_path.name.lower()
                    clean_dir_name = re.sub(r'[^a-z0-9]', '', dir_name)
                    
                    # Increase score for directories that match game name
                    if clean_dir_name == clean_game_name:
                        score += 3  # Exact match
                    elif clean_game_name in clean_dir_name or clean_dir_name in clean_game_name:
                        score += 2  # Partial match
                    elif dir_name in ['bin', 'bin64', 'bin32', 'binaries', 'game', 'main']:
                        score += 2  # Common game directories
                    elif any(term in dir_name for term in ['redist', 'setup', 'support', 'tools', 'eadm']):
                        score -= 2  # Negative indicators
                    
                    # Analyze subdirectory names
                    subdirs = [d for d in dir_path.iterdir() if d.is_dir()]
                    subdir_names = [d.name.lower() for d in subdirs]
                    
                    # Game directories often have these subdirectories
                    game_subdir_indicators = ['data', 'config', 'save', 'content', 'assets', 'levels']
                    for indicator in game_subdir_indicators:
                        if any(indicator in name for name in subdir_names):
                            score += 0.5
                    
                    # Round to 1 decimal place
                    score = round(score, 1)
                    decky.logger.debug(f"Directory content score for {dir_path}: {score}")
                    return score
                    
                except (PermissionError, OSError) as e:
                    decky.logger.debug(f"Error analyzing directory {dir_path}: {e}")
                    return 0
            
            def score_executable(exe_path: Path) -> float:
                """Score an executable based on how likely it is to be the main game executable"""
                if not exe_path.is_file():
                    return 0
                    
                name = exe_path.stem.lower()
                score = 0
                
                # Skip utility executables
                if any(skip in name for skip in ["unins", "launcher", "crash", "setup", "config", "redist", "install"]):
                    return 0
                    
                decky.logger.debug(f"Scoring executable: {name}")
                
                # Enhanced name matching for specific cases
                clean_exe_name = re.sub(r'[^a-z0-9]', '', name)
                
                # Check exact match (normalized)
                if clean_exe_name == clean_game_name:
                    exact_match_score = 30
                    decky.logger.debug(f"  Exact normalized match: +{exact_match_score}")
                    score += exact_match_score
                
                # Handle special cases like "among us.exe" vs "amongus"
                elif name.replace(" ", "") == game_name.lower() or game_name.lower().replace(" ", "") == name:
                    special_match_score = 25
                    decky.logger.debug(f"  Special space-normalized match: +{special_match_score}")
                    score += special_match_score
                
                # Check partial matches
                elif clean_game_name in clean_exe_name or clean_exe_name in clean_game_name:
                    # Calculate how much of the string matches
                    match_ratio = max(
                        len(clean_game_name) / len(clean_exe_name) if len(clean_exe_name) > 0 else 0,
                        len(clean_exe_name) / len(clean_game_name) if len(clean_game_name) > 0 else 0
                    )
                    # Scale the score (max 20 points)
                    partial_score = min(20, int(match_ratio * 20))
                    score += partial_score
                    decky.logger.debug(f"  Partial name match: +{partial_score} (ratio: {match_ratio:.2f})")
                
                # Word-based matching
                else:
                    # Name matching with game name
                    name_words = set(re.findall(r'\w+', name))
                    
                    # Calculate word match score based on intersection
                    matching_words = game_words.intersection(name_words)
                    
                    # If there are matching words, they're worth MUCH more if they're a larger percentage of the game name
                    if matching_words:
                        match_percentage = len(matching_words) / len(game_words) if game_words else 0
                        word_score = len(matching_words) * 5.0 * (1 + match_percentage)  # Increased from 1.5 to 5.0
                        word_score = min(15, round(word_score, 1))  # Cap at 15 and round
                        decky.logger.debug(f"  Name match score: +{word_score} (words: {matching_words})")
                        score += word_score
                
                # Bonus for common game executable names (increased)
                if name.lower() in ["game", "start", "play", "client", "app"]:
                    common_name_score = 5.0  # Increased from 0.5 to 5.0
                    decky.logger.debug(f"  Common name bonus: +{common_name_score} ({name})")
                    score += common_name_score
                
                try:
                    # File size is still a factor, but MUCH less important than name matching
                    size = exe_path.stat().st_size
                    size_mb = size / (1024 * 1024)
                    
                    # Reduced logarithmic scoring for size - much lower weight
                    if size_mb > 0:
                        import math
                        size_score = min(0.5, math.log10(size_mb) / 6)  # Significantly reduced weight for size
                        size_score = round(size_score, 1)  # Round to 1 decimal
                        decky.logger.debug(f"  Size score: +{size_score} ({size_mb:.2f} MB)")
                        score += size_score
                    
                    # Smaller penalty for extremely small executables
                    if size_mb < 0.5:  # Less than 500KB
                        size_penalty = 0.5  # Reduced from 1
                        decky.logger.debug(f"  Small size penalty: -{size_penalty}")
                        score -= size_penalty
                except Exception as e:
                    decky.logger.debug(f"  Error checking file size: {e}")
                
                # If the name contains "launcher" or "setup", reduce score significantly
                if "launcher" in name.lower() or "setup" in name.lower():
                    launcher_penalty = 10  # Increased from 3 to 10
                    decky.logger.debug(f"  Launcher/setup penalty: -{launcher_penalty}")
                    score -= launcher_penalty
                
                # Round score to 1 decimal place
                score = round(score, 1)
                
                decky.logger.debug(f"  Final executable score: {score}")
                return score
            
            def find_best_exe_dir(path: Path, max_depth=3, current_depth=0) -> tuple[Path, float]:
                """Recursively find the best executable directory"""
                if not path.exists() or not path.is_dir():
                    return None, 0
                    
                best_exe_dir = None
                best_score = -1
                
                try:
                    # First check for executables in this directory
                    exes_in_dir = []
                    for exe in path.glob("*.exe"):
                        exe_score = score_executable(exe)
                        if exe_score > 0:
                            exes_in_dir.append((exe, exe_score))
                    
                    # Get directory content score
                    dir_content_score = analyze_directory_content(path)
                    
                    # Sort executables by score (highest first)
                    exes_in_dir.sort(key=lambda x: x[1], reverse=True)
                    
                    # Calculate combined score for this directory
                    if exes_in_dir:
                        best_exe_score = exes_in_dir[0][1]
                        combined_score = best_exe_score + dir_content_score
                        decky.logger.debug(f"Directory {path} - Best exe: {exes_in_dir[0][0].name} (score: {best_exe_score:.1f}), Dir content: {dir_content_score:.1f}, Combined: {combined_score:.1f}")
                        
                        if combined_score > best_score:
                            best_score = combined_score
                            best_exe_dir = path
                    else:
                        # If no executables, just use the directory content score
                        if dir_content_score > best_score:
                            best_score = dir_content_score
                            best_exe_dir = path
                    
                    # If we haven't found a good match and have depth remaining, check subdirectories
                    if (best_score < 4 or current_depth == 0) and current_depth < max_depth:
                        for subdir in path.iterdir():
                            if subdir.is_dir():
                                sub_exe_dir, sub_score = find_best_exe_dir(subdir, max_depth, current_depth + 1)
                                if sub_score > best_score:
                                    best_score = sub_score
                                    best_exe_dir = sub_exe_dir
                
                except (PermissionError, OSError) as e:
                    decky.logger.debug(f"Error accessing directory {path}: {e}")
                
                # Round final score to 1 decimal
                best_score = round(best_score, 1)
                
                return best_exe_dir, best_score
                
            # Find the best executable directory
            best_dir, score = find_best_exe_dir(path)
            
            return best_dir, score
            
        except Exception as e:
            decky.logger.error(f"Error in _find_game_executable_directory: {str(e)}")
            return path, 0

    def _find_game_path(self, appid: str) -> str:
        library_file = self._find_libraryfolders_file()
        if library_file is None or not library_file.exists():
            checked = [str(path / "steamapps" / "libraryfolders.vdf") for path in self._steam_root_candidates()]
            raise ValueError(f"Steam library file not found. Checked: {', '.join(checked)}")

        library_paths = self._steam_library_paths(library_file)

        for library_path in library_paths:
            manifest_path = Path(library_path) / "steamapps" / f"appmanifest_{appid}.acf"
            if manifest_path.exists():
                with open(manifest_path, "r", encoding="utf-8") as manifest:
                    for line in manifest:
                        if '"installdir"' in line:
                            install_dir = line.split('"installdir"')[1].strip().strip('"')
                            base_path = Path(library_path) / "steamapps" / "common" / install_dir
                            
                            # Get name of the game directory for smarter exe matching
                            game_name = install_dir.lower().replace("_", " ").replace("-", " ")
                            
                            decky.logger.info(f"Finding executable directory for Steam game: {game_name}")
                            
                            # Use the unified game executable detection function
                            best_dir, score = self._find_game_executable_directory(base_path, game_name)
                            
                            if best_dir and score > 0:
                                decky.logger.info(f"Found game executable directory: {best_dir} (score: {score:.2f})")
                                return str(best_dir)
                            
                            # If we couldn't find anything, check some common subdirectories
                            common_dirs = ["bin", "bin32", "bin64", "binaries", "game", "win64", "win32", "x64", "x86"]
                            for common in common_dirs:
                                test_path = base_path / common
                                if test_path.exists() and test_path.is_dir():
                                    exes = list(test_path.glob("*.exe"))
                                    if exes:
                                        decky.logger.info(f"Using common executable directory: {test_path}")
                                        return str(test_path)
                            
                            # If we still didn't find anything, just use the original path
                            decky.logger.info(f"No suitable executable directory found, using base path: {base_path}")
                            return str(base_path)

        raise ValueError(f"Could not find installation directory for AppID: {appid}")

    async def _stop_auto_update_task(self) -> None:
        task = self._auto_update_task
        self._auto_update_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def get_update_status(self) -> dict[str, Any]:
        current = self._current_version()
        if self._cached_update_status and (time.time() - self._last_check_time) < AUTO_CHECK_INTERVAL:
            return {
                **self._cached_update_status,
                "current": current,
                "elevated": self._has_elevated_permissions(),
            }

        return {
            "ok": True,
            "current": current,
            "elevated": self._has_elevated_permissions(),
            "hasUpdate": False,
            "canInstall": False,
            "message": "Ready to check for updates.",
        }

    async def check_update(self, force: bool = False) -> dict[str, Any]:
        if not force and self._cached_update_status and (time.time() - self._last_check_time) < AUTO_CHECK_INTERVAL:
            return self._cached_update_status

        current = self._current_version()
        elevated = self._has_elevated_permissions()
        release = await asyncio.to_thread(self._latest_release)
        if release is None:
            detail = f" {self._last_update_error}" if self._last_update_error else ""
            result = {
                "ok": False,
                "current": current,
                "elevated": elevated,
                "hasUpdate": False,
                "canInstall": False,
                "message": f"Could not read GitHub releases.{detail}",
            }
        else:
            latest = str(release.get("tag_name", "")).removeprefix("v")
            asset = self._release_asset(release)
            has_update = bool(latest and self._is_newer_version(current, latest) and asset)
            result = {
                "ok": True,
                "current": current,
                "latest": latest,
                "elevated": elevated,
                "hasUpdate": has_update,
                "canInstall": bool(asset and elevated),
                "releaseUrl": release.get("html_url", ""),
                "message": (
                    "Update available." if has_update and elevated
                    else "Root permissions are required to install updates." if has_update
                    else "Latest release is already installed."
                ),
            }

        self._cached_update_status = result
        self._update_last_check_time()
        return result

    async def _stop_all_tasks(self) -> None:
        """Cleanly stop all background tasks and close handlers."""
        decky.logger.info("Stopping all plugin tasks for update/restart")
        
        # 1. Stop auto-update task
        await self._stop_auto_update_task()
        
        # 2. Close all per-game loggers
        import logging
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if logger_name.startswith("HDR_"):
                logger = logging.getLogger(logger_name)
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
        
        # 3. Any other cleanup (e.g. active downloads)
        # self._download_task.cancel() if self._download_task else None

    async def install_update(self) -> dict[str, Any]:
        async with self._install_lock:
            status = await self.check_update(force=True)
            if not status.get("ok"):
                return status
            if not status.get("canInstall"):
                return {
                    **status,
                    "ok": False,
                    "message": "No installable update was found, or root permissions are missing.",
                }

            release = await asyncio.to_thread(self._latest_release)
            asset = self._release_asset(release or {})
            if not asset or not asset.get("browser_download_url"):
                return {
                    **status,
                    "ok": False,
                    "message": "No release zip was found.",
                }

            latest = str((release or {}).get("tag_name", "")).removeprefix("v")
            try:
                install_info = await asyncio.wait_for(
                    asyncio.to_thread(self._stage_release_zip, str(asset["browser_download_url"])),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                decky.logger.exception("Update failed - timed out")
                return {**status, "ok": False, "message": "Update failed: staging timed out after 120 seconds."}
            except urllib.error.URLError:
                decky.logger.exception("Update failed - download error")
                return {**status, "ok": False, "message": "Update failed: Could not download release."}
            except zipfile.BadZipFile:
                decky.logger.exception("Update failed - invalid zip")
                return {**status, "ok": False, "message": "Update failed: Release zip was invalid."}
            except Exception as error:
                decky.logger.exception("Update failed")
                return {**status, "ok": False, "message": f"Update failed: {error}"}

            apply_result = self._schedule_update_apply(
                Path(install_info["pluginPath"]),
                Path(install_info["stagingPath"]),
                Path(install_info["backupPath"]),
            )
            if not apply_result.get("scheduled"):
                return {
                    **status,
                    "ok": False,
                    "message": f"Update staged but could not schedule apply helper: {apply_result.get('message')}",
                }

            current = self._current_version()
            decky.logger.info(f"Successfully staged v{latest}; apply helper scheduled.")
            
            result = {
                "ok": True,
                "current": current,
                "latest": latest,
                "installedVersion": install_info.get("installedVersion", current),
                "hasUpdate": False,
                "canInstall": False,
                "elevated": self._has_elevated_permissions(),
                "requiresRestart": True,
                "restarted": True,
                "restart": apply_result,
                "message": f"Update v{latest} staged. Decky Loader will restart to apply it.",
            }
            self._cached_update_status = result
            return result

    async def _auto_check_update(self) -> None:
        try:
            await self.check_update()
        except Exception as error:
            decky.logger.warning("Auto-update check failed: %s", error)

    def _should_auto_check(self) -> bool:
        check_file = Path(decky.DECKY_PLUGIN_DIR) / ".last_update_check"
        try:
            if not check_file.exists():
                return True
            return time.time() - float(check_file.read_text(encoding="utf-8").strip()) > AUTO_CHECK_INTERVAL
        except (OSError, ValueError):
            return True

    def _update_last_check_time(self) -> None:
        try:
            (Path(decky.DECKY_PLUGIN_DIR) / ".last_update_check").write_text(str(time.time()), encoding="utf-8")
        except OSError as error:
            decky.logger.warning("Failed to update last check time: %s", error)

    def _package_version(self, path: Path) -> str:
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(package.get("version", ""))

    def _current_version(self) -> str:
        plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        for candidate in [
            plugin_dir / "package.json",
            Path(__file__).resolve().parent / "package.json",
            Path.cwd() / "package.json",
        ]:
            version = self._package_version(candidate)
            if version:
                return version
        return "unknown"

    def _has_elevated_permissions(self) -> bool:
        if not hasattr(os, "geteuid"):
            return True
        return os.geteuid() == 0

    def _parse_version(self, version: str) -> tuple[int, ...]:
        try:
            clean = version.removeprefix("v").split("-")[0]
            return tuple(int(part) for part in clean.split(".") if part.isdigit())
        except (ValueError, AttributeError):
            return (0,)

    def _is_newer_version(self, current: str, latest: str) -> bool:
        return self._parse_version(latest) > self._parse_version(current)

    def _latest_release(self) -> dict[str, Any] | None:
        self._last_update_error = ""
        releases = self._fetch_json(GITHUB_RELEASES_URL)
        if releases is None:
            return None
        if not isinstance(releases, list):
            self._last_update_error = "GitHub returned an unexpected response."
            return None
        for release in releases:
            if release.get("draft"):
                continue
            if self._release_asset(release):
                return release
        return None

    def _fetch_json(self, url: str) -> Any | None:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": PLUGIN_PACKAGE},
        )
        for context, label in self._ssl_context_candidates():
            try:
                with urllib.request.urlopen(request, timeout=10, context=context) as response:
                    if label == "unverified":
                        decky.logger.warning("Fetched %s without TLS verification (no usable CA store).", url)
                    return json.loads(response.read().decode("utf-8"))
            except (OSError, json.JSONDecodeError, urllib.error.URLError) as error:
                self._last_update_error = f"Python fetch failed ({label} TLS): {error}"

        try:
            clean_env = self._clean_subprocess_env()
            result = subprocess.run(
                ["curl", "-fsSL", "-H", "Accept: application/vnd.github+json", "-A", PLUGIN_PACKAGE, url],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env=clean_env,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._last_update_error += f"; curl failed: {error}"
            return None
        if result.returncode != 0:
            self._last_update_error += f"; curl exited {result.returncode}: {result.stderr.strip()[-120:]}"
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self._last_update_error += f"; curl JSON parse failed: {error}"
            return None

    def _release_asset(self, release: dict[str, Any]) -> dict[str, Any] | None:
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            return None
        for asset in assets:
            name = str(asset.get("name", ""))
            if name.endswith(".zip") and PLUGIN_PACKAGE in name:
                return asset
        return None

    def _stage_release_zip(self, url: str) -> dict[str, Any]:
        plugin_dir = Path(decky.DECKY_PLUGIN_DIR).resolve()
        plugin_parent = plugin_dir.parent
        staging_dir = plugin_parent / f".{plugin_dir.name}.update-{os.getpid()}-{int(time.time())}"
        request = urllib.request.Request(url, headers={"User-Agent": PLUGIN_PACKAGE})

        with tempfile.TemporaryDirectory(prefix=f"{PLUGIN_PACKAGE}-update-") as temp_root:
            temp_path = Path(temp_root)
            archive_path = temp_path / "release.zip"
            self._download_file(request, archive_path)
            extract_dir = temp_path / "extract"
            with zipfile.ZipFile(archive_path) as archive:
                self._safe_extract(archive, extract_dir)

            extracted_plugin = self._find_extracted_plugin(extract_dir)
            if extracted_plugin is None:
                raise ValueError("release zip did not contain a Decky plugin")
            self._validate_extracted_plugin(extracted_plugin)

            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            shutil.copytree(
                extracted_plugin,
                staging_dir,
                symlinks=True,
                ignore=shutil.ignore_patterns("*.log", "__pycache__", ".last_update_check"),
            )
            self._validate_extracted_plugin(staging_dir)
            backup_dir = plugin_dir.with_name(f"{plugin_dir.name}.previous")

            return {
                "installedVersion": self._package_version(staging_dir / "package.json"),
                "pluginPath": str(plugin_dir),
                "backupPath": str(backup_dir),
                "stagingPath": str(staging_dir),
            }

    def _schedule_update_apply(self, plugin_dir: Path, staging_dir: Path, backup_dir: Path) -> dict[str, Any]:
        helper_path = Path(tempfile.gettempdir()) / f"{PLUGIN_PACKAGE}-apply-update-{os.getpid()}-{int(time.time())}.sh"
        log_path = plugin_dir.parent / f".{PLUGIN_PACKAGE}-update.log"
        deck_user = self.environment.get("USER") or getattr(decky, "DECKY_USER", "") or "deck"
        helper = f"""#!/usr/bin/env bash
set -u
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
sleep 1
plugin_dir={shlex.quote(str(plugin_dir))}
staging_dir={shlex.quote(str(staging_dir))}
backup_dir={shlex.quote(str(backup_dir))}
log_path={shlex.quote(str(log_path))}
deck_user={shlex.quote(deck_user)}
{{
  echo "[$(date -Is)] Applying {PLUGIN_PACKAGE} update"
  if [ ! -d "$staging_dir" ]; then
    echo "Missing staged update: $staging_dir"
    exit 1
  fi
  systemctl --user stop plugin_loader.service || systemctl --user stop plugin_loader || systemctl stop plugin_loader.service || systemctl stop plugin_loader || true
  sleep 1
  rm -rf "$backup_dir"
  if [ -e "$plugin_dir" ]; then
    mkdir -p "$backup_dir"
    (cd "$plugin_dir" && tar cf - .) | (cd "$backup_dir" && tar xf -) || true
  else
    mkdir -p "$plugin_dir"
  fi
  if [ -d "$staging_dir/dist" ]; then
    mkdir -p "$plugin_dir/dist"
    cp -af "$staging_dir/dist/." "$plugin_dir/dist/"
  fi
  if [ -d "$staging_dir/backend" ]; then
    rm -rf "$plugin_dir/backend"
    cp -af "$staging_dir/backend" "$plugin_dir/backend"
  fi
  if [ -d "$staging_dir/defaults" ]; then
    rm -rf "$plugin_dir/defaults"
    cp -af "$staging_dir/defaults" "$plugin_dir/defaults"
  fi
  if cp -af "$staging_dir"/plugin.json "$staging_dir"/main.py "$staging_dir"/package.json "$staging_dir"/README.md "$plugin_dir"/ 2>/dev/null; then
    [ -f "$staging_dir/LICENSE" ] && cp -af "$staging_dir/LICENSE" "$plugin_dir/LICENSE"
    rm -rf "$staging_dir"
    chown -R "$deck_user:$deck_user" "$plugin_dir" 2>/dev/null || true
    echo "Update copy complete"
  else
    echo "Update copy failed, attempting rollback"
    rm -rf "$plugin_dir/backend" "$plugin_dir/defaults"
    if [ -e "$backup_dir" ]; then
      cp -af "$backup_dir/." "$plugin_dir/"
    fi
  fi
  systemctl --user start plugin_loader.service || systemctl --user start plugin_loader || systemctl start plugin_loader.service || systemctl start plugin_loader || true
  rm -f "$0"
}} >> "$log_path" 2>&1
"""
        try:
            helper_path.write_text(helper, encoding="utf-8")
            helper_path.chmod(0o755)
            result = subprocess.run(
                ["bash", "-lc", f"nohup {shlex.quote(str(helper_path))} >/dev/null 2>&1 &"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=self._clean_subprocess_env(),
            )
            if result.returncode != 0:
                return {"scheduled": False, "method": "helper", "message": result.stderr.strip()[-160:]}
            return {"scheduled": True, "method": "stop-swap-start-helper", "message": "Update apply helper scheduled."}
        except Exception as error:
            return {"scheduled": False, "method": "helper", "message": str(error)}

    def _install_release_zip(self, url: str) -> dict[str, Any]:
        staged = self._stage_release_zip(url)
        plugin_dir = Path(staged["pluginPath"])
        staging_dir = Path(staged["stagingPath"])
        backup_dir = Path(staged["backupPath"])
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        moved_existing = False
        try:
            if plugin_dir.exists():
                plugin_dir.rename(backup_dir)
                moved_existing = True
            staging_dir.rename(plugin_dir)
        except OSError:
            decky.logger.exception("Update replacement failed, attempting rollback")
            if moved_existing and plugin_dir.exists():
                shutil.rmtree(plugin_dir)
            if moved_existing and backup_dir.exists() and not plugin_dir.exists():
                backup_dir.rename(plugin_dir)
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise
        self._fix_deck_user_ownership(plugin_dir)
        return {"installedVersion": self._package_version(plugin_dir / "package.json"), "backupPath": str(backup_dir)}

    def _find_extracted_plugin(self, root: Path) -> Path | None:
        for candidate in [root, *root.iterdir()]:
            if candidate.is_dir() and (candidate / "plugin.json").exists() and (candidate / "package.json").exists():
                return candidate
        return None

    def _validate_extracted_plugin(self, plugin_path: Path) -> None:
        required = [
            plugin_path / "plugin.json",
            plugin_path / "package.json",
            plugin_path / "dist" / "index.js",
            plugin_path / "main.py",
            # Backend modules are imported by main.py at runtime; ensure releases include them.
            plugin_path / "backend" / "__init__.py",
            plugin_path / "backend" / "cache.py",
        ]
        for required_path in required:
            if not required_path.exists():
                raise ValueError(f"release zip is missing {required_path.relative_to(plugin_path)}")

        try:
            manifest = json.loads((plugin_path / "plugin.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"release plugin.json is invalid: {error}") from error
        if str(manifest.get("name", "")) != PLUGIN_NAME:
            raise ValueError(f"release zip is not {PLUGIN_NAME}")

        try:
            package = json.loads((plugin_path / "package.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"release package.json is invalid: {error}") from error
        if package.get("name") != PLUGIN_PACKAGE:
            raise ValueError(f"release package name is not {PLUGIN_PACKAGE}")
        if not package.get("version"):
            raise ValueError("release package.json does not contain a version")

    def _safe_extract(self, archive: zipfile.ZipFile, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        resolved_target = target.resolve()
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(resolved_target):
                raise ValueError(f"Attempted path traversal in zip: {member.filename}")
        archive.extractall(target)

    def _download_file(self, request: urllib.request.Request, target: Path) -> None:
        for context, label in self._ssl_context_candidates():
            try:
                with urllib.request.urlopen(request, timeout=45, context=context) as response:
                    target.write_bytes(response.read())
                    if label == "unverified":
                        decky.logger.warning("Downloaded %s without TLS verification (no usable CA store).", request.full_url)
                    return
            except (OSError, urllib.error.URLError) as error:
                decky.logger.warning("Python download failed (%s TLS): %s", label, error)

        result = subprocess.run(
            ["curl", "-fL", "--retry", "2", "--connect-timeout", "20", "-A", "Mozilla/5.0 DeckyRenoDX/1.0", "-o", str(target), request.full_url],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=self._clean_subprocess_env(),
        )
        if result.returncode != 0:
            raise urllib.error.URLError(result.stderr.strip() or f"curl exited {result.returncode}")

    def _clean_subprocess_env(self) -> dict[str, str]:
        clean_env = {**os.environ, **self.environment}
        for key in ["LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"]:
            clean_env.pop(key, None)
        return clean_env

    def _schedule_loader_restart(self, reason: str) -> dict[str, Any]:
        decky.logger.info("Scheduling Decky Loader restart: %s", reason)
        unit = f"{PLUGIN_PACKAGE}-restart-{int(time.time())}"
        
        # Primary method: systemd-run (delayed, detached)
        cmd = ["systemd-run", "--user", "--on-active=2s", f"--unit={unit}", "systemctl", "--user", "restart", "plugin_loader.service"]
        
        try:
            # Use Popen to detach completely and avoid waiting on the command
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return {"scheduled": True, "method": "systemd-run-user", "message": "Decky Loader restart scheduled in 2s."}
        except Exception as e:
            decky.logger.warning(f"systemd-run failed: {e}")

        # Fallback: helper script
        return self._schedule_loader_restart_helper()

    def _schedule_loader_restart_helper(self) -> dict[str, Any]:
        helper_path = Path(tempfile.gettempdir()) / f"{PLUGIN_PACKAGE}-restart-{os.getpid()}-{int(time.time())}.sh"
        helper = f"""#!/usr/bin/env bash
sleep 2
systemctl --user restart plugin_loader.service || systemctl restart plugin_loader.service
rm -f "{helper_path}"
"""
        try:
            helper_path.write_text(helper, encoding="utf-8")
            helper_path.chmod(0o755)
            # Spawn the helper in its own session
            subprocess.Popen([str(helper_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return {"scheduled": True, "method": "helper", "message": "Decky Loader restart helper started."}
        except Exception as e:
            decky.logger.error(f"Helper restart failed: {e}")
            return {"scheduled": False, "method": "helper", "message": str(e)}

    def _cleanup_previous_update_artifacts(self) -> None:
        plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        plugin_parent = plugin_dir.parent
        backup_dir = plugin_dir.with_name(f"{plugin_dir.name}.previous")
        for path in [backup_dir, *plugin_parent.glob(f".{plugin_dir.name}.update-*")]:
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                decky.logger.info("Removed previous update artifact: %s", path)
            except OSError as error:
                decky.logger.warning("Failed to remove update artifact %s: %s", path, error)

    async def log_error(self, error: str) -> None:
        decky.logger.error(f"FRONTEND: {error}")

    async def open_renodx_search(self, game_name: str = "") -> dict:
        """Open a browser search that helps the user fetch Nexus/GitHub-hosted RenoDX files."""
        try:
            if re.match(r"^https?://", game_name or ""):
                url = game_name
            else:
                query = f"{game_name} RenoDX NexusMods".strip() if game_name else "RenoDX NexusMods"
                url = "https://www.google.com/search?q=" + query.replace(" ", "+")
            clean_env = {**os.environ, **self.environment}
            clean_env["LD_LIBRARY_PATH"] = ""
            subprocess.Popen(["xdg-open", url], env=clean_env)
            return {
                "status": "success",
                "url": url,
                "message": "Browser opened. Download the RenoDX addon/archive, then return here and import it.",
            }
        except Exception as e:
            decky.logger.error(f"Failed to open browser: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def find_recent_renodx_downloads(self) -> dict:
        """Find likely RenoDX files downloaded by the user."""
        try:
            candidates = []
            search_dirs = [
                self._deck_user_home() / "Downloads",
                self._deck_user_home() / "downloads",
                Path(self.renodx_import_path),
            ]
            valid_exts = {".zip", ".7z", ".rar", ".addon64", ".addon32"}
            for directory in search_dirs:
                if not directory.exists():
                    continue
                for item in directory.iterdir():
                    if not item.is_file() or item.suffix.lower() not in valid_exts:
                        continue
                    name = item.name.lower()
                    if "renodx" not in name and "addon" not in name and "hdr" not in name:
                        continue
                    candidates.append({
                        "path": str(item),
                        "name": item.name,
                        "size": item.stat().st_size,
                        "modified": item.stat().st_mtime,
                    })
            candidates.sort(key=lambda entry: entry["modified"], reverse=True)
            return {"status": "success", "files": candidates[:20]}
        except Exception as e:
            decky.logger.error(f"RenoDX scan failed: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def import_renodx_for_game(self, appid: str, selected_file: str = "", selected_executable_path: str = "") -> dict:
        """Copy a user-downloaded RenoDX addon into the detected game executable directory."""
        try:
            if selected_executable_path and os.path.exists(selected_executable_path):
                game_path = os.path.dirname(selected_executable_path)
            else:
                detection = await self.find_game_executable_path(appid)
                if detection.get("status") == "success":
                    steam_result = detection.get("steam_logs_result", {})
                    enhanced_result = detection.get("enhanced_detection_result", {})
                    exe_path = steam_result.get("executable_path") or enhanced_result.get("executable_path")
                    if exe_path:
                        game_path = os.path.dirname(exe_path)
                    else:
                        game_path = self._find_game_path(appid)
                else:
                    game_path = self._find_game_path(appid)

            source = Path(selected_file) if selected_file else None
            if not source or not source.exists():
                recent = await self.find_recent_renodx_downloads()
                files = recent.get("files", [])
                if not files:
                    return {"status": "error", "message": "No RenoDX addon/archive found in Downloads."}
                source = Path(files[0]["path"])

            copied = []
            target_dir = Path(game_path)
            if source.suffix.lower() in [".addon64", ".addon32"]:
                target = target_dir / source.name
                shutil.copy2(source, target)
                copied.append(str(target))
            elif source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source) as archive:
                    members = [
                        name for name in archive.namelist()
                        if name.lower().endswith((".addon64", ".addon32", ".ini", ".fx", ".fxh"))
                    ]
                    if not members:
                        return {"status": "error", "message": "Archive did not contain RenoDX/ReShade addon files."}
                    extract_dir = Path(self.renodx_import_path) / source.stem
                    if extract_dir.exists():
                        shutil.rmtree(extract_dir)
                    archive.extractall(extract_dir)
                    for member in members:
                        extracted = extract_dir / member
                        if extracted.is_file() and extracted.suffix.lower() in [".addon64", ".addon32"]:
                            target = target_dir / extracted.name
                            shutil.copy2(extracted, target)
                            copied.append(str(target))
            else:
                return {"status": "error", "message": "Only .addon64, .addon32, and .zip imports are supported right now."}

            if not copied:
                return {"status": "error", "message": "No RenoDX addon file was copied."}

            self._fix_deck_user_ownership(target_dir)
            return {
                "status": "success",
                "output": f"Imported RenoDX files to {game_path}",
                "copied": copied,
                "launch_options": self._hdr_launch_options("dxgi", appid, "renodx"),
            }
        except Exception as e:
            decky.logger.error(f"RenoDX import failed: {str(e)}")
            return {"status": "error", "message": str(e)}
