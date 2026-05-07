import os
import shutil
import configparser
import time
from pathlib import Path

class HDRInstaller:
    def __init__(self, manifest_manager, logger=None):
        self.manifest_manager = manifest_manager
        self.logger = logger

    def install_special_k(self, appid, exe_path, sk_source_path, delay_seconds="0", notes=None):
        """Install Special K to the game directory."""
        if not os.path.exists(exe_path):
            return False, "Executable path not found."
            
        game_dir = os.path.dirname(exe_path)
        dll_name = "dxgi.dll" # Default for SK
        target_dll = os.path.join(game_dir, dll_name)
        
        manifest_data = {
            "appid": appid,
            "method": "special_k",
            "installed_files": [],
            "modified_files": [],
            "backups": {},
            "pcgamingwiki_notes": notes or [],
            "verified": False
        }

        try:
            # 1. Backup existing DLL if it exists
            if os.path.exists(target_dll):
                backup_path = f"{target_dll}.bak"
                shutil.move(target_dll, backup_path)
                manifest_data["backups"][target_dll] = backup_path
                if self.logger: self.logger.info(f"Backed up existing {dll_name}")

            # 2. Copy Special K DLL
            shutil.copy(sk_source_path, target_dll)
            manifest_data["installed_files"].append(target_dll)
            
            # 3. Create/Modify SpecialK.ini with OLED defaults
            ini_path = os.path.join(game_dir, "SpecialK.ini")
            self._apply_sk_oled_defaults(ini_path, delay_seconds=delay_seconds)
            manifest_data["installed_files"].append(ini_path)
            stale_dll_ini = os.path.join(game_dir, f"{Path(dll_name).stem}.ini")
            if os.path.exists(stale_dll_ini):
                os.remove(stale_dll_ini)
                if self.logger: self.logger.info(f"Removed stale {Path(stale_dll_ini).name} from previous Special K install path")
            # 4. Save manifest
            self.manifest_manager.write_manifest(appid, manifest_data)
            
            return True, "Special K installed. Please launch game to verify."
        except Exception as e:
            if self.logger: self.logger.error(f"Special K install failed: {str(e)}")
            return False, str(e)

    def _apply_sk_oled_defaults(self, ini_path, delay_seconds="0"):
        """Apply Steam Deck OLED defaults to SpecialK.ini."""
        config = configparser.ConfigParser(strict=False)
        if os.path.exists(ini_path):
            config.read(ini_path)
            
        # Ensure sections exist
        if 'HDR.Settings' not in config:
            config['HDR.Settings'] = {}
            
        # OLED Defaults
        config['HDR.Settings']['PeakLuminance'] = '1000'
        config['HDR.Settings']['PaperWhiteLuminance'] = '200'
        config['HDR.Settings']['UsePrecomputedLuminance'] = 'true'
        
        # Scaling for Deck Screen
        if 'Display.Output' not in config:
            config['Display.Output'] = {}
        config['Display.Output']['UIScale'] = '0.8' # Scale down for Deck

        if 'SpecialK.System' not in config:
            config['SpecialK.System'] = {}
        config['SpecialK.System']['UsingWINE'] = 'true'
        config['SpecialK.System']['InjectionDelay'] = str(delay_seconds or "0")
        for section in ["Steam.System", "Steam.Log"]:
            if section in config:
                del config[section]

        with open(ini_path, 'w') as configfile:
            config.write(configfile)

    def verify_special_k(self, game_dir):
        """Check if Special K successfully initialized."""
        # Special K creates logs in a 'logs' subdirectory or game root
        log_candidates = [
            os.path.join(game_dir, "logs", "dxgi.log"),
            os.path.join(game_dir, "logs", "SpecialK.log"),
            os.path.join(game_dir, "dxgi.log"),
            os.path.join(game_dir, "SpecialK.log")
        ]
        
        for log_path in log_candidates:
            if os.path.exists(log_path):
                # Check if log is recent (within last 2 minutes)
                mtime = os.path.getmtime(log_path)
                if (time.time() - mtime) < 120:
                    return True, f"Verified success via log: {os.path.basename(log_path)}"
                    
        return False, "No recent Special K logs detected. Injection might have failed or HDR is unsupported."

    def verify_reshade(self, game_dir):
        """Check if ReShade successfully initialized."""
        required = [
            "ReShade.ini",
            "ReShadePreset.ini",
            "ReShade_shaders",
        ]
        missing = [name for name in required if not os.path.exists(os.path.join(game_dir, name))]
        if missing:
            return False, f"ReShade install is incomplete. Missing: {', '.join(missing)}"

        dlls = ["dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll"]
        if not any(os.path.exists(os.path.join(game_dir, name)) for name in dlls):
            return False, "ReShade DLL not found in the game directory."

        shader_root = Path(game_dir) / "ReShade_shaders"
        shader_files = list(shader_root.rglob("*.fx")) + list(shader_root.rglob("*.fxh")) if shader_root.exists() else []
        if not shader_files:
            return False, "ReShade shader folder exists, but no HDR shader files were found."

        log_candidates = [
            os.path.join(game_dir, "ReShade.log"),
            os.path.join(game_dir, "dxgi.log"),
            os.path.join(game_dir, "d3d11.log"),
            os.path.join(game_dir, "d3d12.log"),
            os.path.join(game_dir, "d3d9.log"),
        ]
        for log_path in log_candidates:
            if os.path.exists(log_path):
                with open(log_path, "r", errors="ignore") as f:
                    content = f.read(4096)
                    if "ReShade" in content:
                        return True, f"Verified ReShade via {os.path.basename(log_path)}."

        return True, "ReShade files and HDR shaders are installed. Launch the game once to generate ReShade.log for runtime verification."

    def uninstall(self, appid):
        """Use manifest to remove HDR."""
        return self.manifest_manager.remove_hdr(appid, self.logger)
