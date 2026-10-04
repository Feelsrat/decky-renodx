"""The plugin's operations. Synchronous; main.py runs them in threads behind per-game locks."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import detect, display, fsutil, installers, launch, log, net, recommend, renodx, rhi, state, transaction
from .compat import CompatDB
from .config import RESHADE_VERSION, Paths
from .installers import InstallError, Target
from .pcgw import PCGamingWiki
from .renodx import RenoDXCatalog
from .renodx_index import RenoDXIndex
from .runtime import Runtime
from .steam import SteamApp, SteamLibrary, running_appids, valid_appid

METHOD_LABELS = {
    "renodx": "RenoDX", "special_k": "Special K", "special_k_delayed": "Special K Delayed",
    "reshade": "ReShade AutoHDR", "native_hdr": "Native HDR", "sdr": "SDR",
}
METHOD_ALIASES = {"specialk": "special_k", "specialk-delayed": "special_k_delayed", "reshade_autohdr": "reshade", "reshade-hdr": "reshade"}
IMPORT_EXTENSIONS = {".addon64", ".addon32", ".zip", ".7z", ".rar"}


class ServiceError(Exception):
    pass


class ManualDownload(Exception):
    def __init__(self, mod: dict[str, Any]):
        super().__init__("This RenoDX mod has to be downloaded manually.")
        self.mod = mod


def _ok(**fields: Any) -> dict[str, Any]:
    return {"status": "success", **fields}


def _err(message: str, **fields: Any) -> dict[str, Any]:
    return {"status": "error", "message": message, **fields}


class HdrService:
    def __init__(self, paths: Paths, version: str):
        self.paths = paths
        self.version = version
        fsutil.set_owner(paths.user)
        for directory in (paths.data, paths.cache, paths.installs, paths.logs):
            fsutil.makedirs(directory)
        log.configure(paths.logs)
        self.steam = SteamLibrary(paths.home)
        self.compat = CompatDB(paths.plugin_dir / "compatibility.json", paths.compat_cache)
        self.renodx = RenoDXCatalog(paths.cache / "renodx_mods.json", lambda url: net.fetch_text(url, timeout=20))
        self._engines: dict[str, tuple[tuple[str, str], str]] = {}
        self._prefetch_lock = threading.Lock()
        self._prefetching: set[str] = set()
        self.rhi = rhi.RhiManifest(paths.cache / "rhi_manifest.json", lambda url: net.fetch_text(url, timeout=20))
        self.renodx_index = RenoDXIndex(paths.cache, lambda url: net.fetch_text(url, timeout=20))
        self.pcgw = PCGamingWiki(paths.cache / "pcgamingwiki.json", net.fetch_json)
        self.runtime = Runtime(paths)
        self.store = state.InstallStore(paths.installs)
        self.settings = state.Settings(paths.settings_file)

    # ------------------------------------------------------------ games
    def list_games(self) -> dict[str, Any]:
        """Installed games, each marked when the RenoDX wiki lists a mod for it (exact name)."""
        games = [app.to_dict() for app in self.steam.games()]
        for game in games:
            game["renodx"] = False
        try:
            for game in games:
                titles = [game["name"], *self.rhi.game(game["name"]).get("aliases", [])]
                appid = game["appid"] if game["kind"] == "steam" else ""
                game["renodx"] = self.renodx.listed(titles) is not None or self.renodx_index.has(appid, titles)
        except Exception as error:  # mod list unavailable: just no marks
            log.plugin().info("No RenoDX marks for the game list: %s", error)
        return _ok(games=games)

    def badge(self, appid: str, title: str = "") -> dict[str, Any]:
        """What the library badge shows: HDR set up, a RenoDX mod, likely via an engine addon, or native HDR."""
        if not valid_appid(appid):
            return _err(f"Invalid AppID: {appid}")
        return _ok(**self._badge(appid, title, self.steam.app(appid), cached_only=False))

    def badges(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Badges for the library grid, many at once. Only uses data already at hand
        (no PCGamingWiki requests), so a screen of tiles stays quick."""
        apps = {app.appid: app for app in self.steam.games()}
        result: dict[str, dict[str, Any]] = {}
        unknown: list[str] = []
        for item in items[:200]:
            appid = str((item or {}).get("appid", ""))
            if not valid_appid(appid) or appid in result:
                continue
            app = apps.get(appid)
            badge = self._badge(appid, str(item.get("title") or ""), app, cached_only=True)
            result[appid] = {"level": badge["level"], "label": badge.get("label", "")}
            shortcut = bool(app and app.is_shortcut) or int(appid) >= 0x80000000
            if badge["level"] in {"none", "engine"} and not shortcut and self.pcgw.cached(appid) is None:
                unknown.append(appid)
        # PCGamingWiki (native HDR, engine) is looked up in bulk in the background; the
        # grid asks again for ``pending`` games once that has had time to finish.
        return _ok(badges=result, pending=self._prefetch_pcgw(unknown))

    def _prefetch_pcgw(self, appids: list[str]) -> list[str]:
        with self._prefetch_lock:
            new = [appid for appid in appids if appid not in self._prefetching]
            self._prefetching.update(new)
            pending = [appid for appid in appids if appid in self._prefetching]
        if new:
            def work() -> None:
                try:
                    self.pcgw.prefetch(new)
                finally:
                    with self._prefetch_lock:
                        self._prefetching.difference_update(new)
            threading.Thread(target=work, name="pcgw-prefetch", daemon=True).start()
        return pending

    def _badge(self, appid: str, title: str, app: SteamApp | None, *, cached_only: bool) -> dict[str, Any]:
        record = self.store.get(appid)
        if record:
            name = METHOD_LABELS.get(record["method"], record["method"])
            return {"level": "on", "label": "HDR on", "detail": f"{name} is set up by Decky RenoDX."}
        title = app.name if app else title
        match = None
        fixes = self.rhi.game(title) if title else {}
        if title:
            try:
                # The grid asks for dozens at once: exact names only there; the game page also matches editions.
                found = self.renodx.listed([title, *fixes.get("aliases", [])]) if cached_only else self.renodx.match(title, aliases=fixes.get("aliases"))
                match = rhi.apply(found, fixes, title)
            except Exception:  # mod list unavailable
                match = None
        if not match or match.get("match_type") not in {"specific", "generic_listed"}:
            shortcut = bool(app and app.is_shortcut)
            match = self.renodx_index.find("" if shortcut else appid, [title, *fixes.get("aliases", [])] if title else []) or match
        if match and match.get("match_type") in {"specific", "generic_listed"}:
            wip = match.get("status") == "in_progress"
            return dict(level="renodx", label="RenoDX WIP" if wip else "RenoDX",
                        detail=f"RenoDX has a mod for this game{' (work in progress)' if wip else ''}: {match.get('name', title)}.")
        if app and app.is_shortcut:
            wiki = {}
        else:
            wiki = (self.pcgw.cached(appid) or {}) if cached_only else self.pcgw.game_data(appid)
        if str(wiki.get("native_hdr", "")).lower() in {"true", "limited", "good", "yes"}:
            return {"level": "native", "label": "Native HDR", "detail": "PCGamingWiki says the game has its own HDR."}
        engine = self._engine(app) if app else "unknown"
        if engine == "unknown":
            engine = renodx.engine_bucket(wiki.get("engine", "")) or "unknown"
        if engine in {"unreal", "unity"}:
            return {"level": "engine", "label": f"RenoDX? ({engine.title()})",
                    "detail": f"No game-specific mod, but RenoDX's generic {engine.title()} addon often works (experimental)."}
        return {"level": "none"}

    def _engine(self, app: SteamApp) -> str:
        key = (str(app.install_path), app.buildid)
        cached = self._engines.get(app.appid)
        if cached and cached[0] == key:
            return cached[1]
        engine = detect.quick_engine(app.install_path) if app.install_path.is_dir() else "unknown"
        self._engines[app.appid] = (key, engine)
        return engine

    def _app(self, appid: str) -> SteamApp:
        if not valid_appid(appid):
            raise ServiceError(f"Invalid AppID: {appid}")
        app = self.steam.app(appid)
        if app is None:
            raise ServiceError(f"AppID {appid} is not installed in any Steam library. Install the game, then set up HDR here.")
        return app

    def _scan(self, app: SteamApp) -> detect.GameScan:
        record = self.store.get(app.appid)
        override = str(self.settings.game(app.appid).get("exe") or "")
        note = "Using the executable you selected."
        if not override and record and Path(record.get("exe_path", "")).is_file():
            override, note = record["exe_path"], "Using the executable HDR was installed for."
        elif not override and app.exe:
            override, note = app.exe, "Using the shortcut's executable."
        exclude = set(record.get("created", [])) if record else set()
        fixes = self.rhi.game(app.name)
        if not override:
            override = self._rhi_exe(app, fixes, exclude)
            note = "Using the executable RHI's game data names."
        launched = None if override else self.steam.launched_executable(app.appid, app.install_path)
        scan = detect.scan_game(app.install_path, app.name, exe_override=override, override_note=note, launched_exe=launched, exclude=exclude)
        if fixes.get("arch") and scan.exe_path and scan.architecture != fixes["arch"]:
            scan.architecture = fixes["arch"]
        if fixes.get("api") and scan.exe_path and scan.api != fixes["api"]:
            scan.api, scan.api_confidence, scan.api_source = fixes["api"], "high", "rhi"
            scan.hook = detect.hook_for_api(scan.api)
        forced = self.compat.forced_api(app.appid)
        if forced:
            scan.api, scan.api_confidence, scan.api_source = forced, "high", "compatibility_db"
            scan.hook = detect.hook_for_api(forced)
        return scan

    def _rhi_exe(self, app: SteamApp, fixes: dict[str, Any], exclude: set[str]) -> str:
        """The executable RHI's data points at: a named .exe, or the best .exe in a named folder."""
        if not (fixes.get("exe_name") or fixes.get("install_dirs")):
            return ""
        candidates = detect.find_executables(app.install_path, app.name, exclude)
        if fixes.get("exe_name"):
            named = [c for c in candidates if Path(c.path).name.lower() == fixes["exe_name"].lower()]
            if named:
                return named[0].path
        for folder in fixes.get("install_dirs") or []:
            directory = (app.install_path / folder).resolve()
            inside = [c for c in candidates if Path(c.path).parent.resolve() == directory]
            if inside:
                return inside[0].path
        return ""

    def _context(self, app: SteamApp, scan: detect.GameScan) -> dict[str, Any]:
        # Non-Steam shortcut ids mean nothing to PCGamingWiki.
        wiki = {} if app.is_shortcut else self.pcgw.game_data(app.appid)
        weak = scan.api == "unknown" or scan.api_source.startswith("dll_imports") or scan.api_source.endswith("_engine")
        if weak and scan.api_source != "compatibility_db" and wiki.get("graphics_api", "unknown") not in {"unknown", scan.api}:
            # A runtime DLL's imports (or an engine guess) are weaker evidence than PCGamingWiki's API list.
            scan.api, scan.api_confidence, scan.api_source = wiki["graphics_api"], "metadata", "pcgamingwiki"
            scan.hook = detect.hook_for_api(scan.api)
        engine = scan.engine if scan.engine != "unknown" else (renodx.engine_bucket(wiki.get("engine", "")) or "unknown")
        match: dict[str, Any] | None = None
        renodx_error = ""
        fixes = self.rhi.game(app.name)
        try:
            match = self.renodx.match(app.name, aliases=fixes.get("aliases"), engine=engine, architecture=scan.architecture)
        except Exception as error:
            renodx_error = f"RenoDX mod list unavailable: {error}"
        match = rhi.apply(match, fixes, app.name)
        if not match or match.get("match_type") == "generic_engine":
            # Not on the wiki: RenoDX's build index knows mods by Steam AppID.
            indexed = self.renodx_index.find("" if app.is_shortcut else app.appid, [app.name, *fixes.get("aliases", [])], scan.architecture)
            match = indexed or match
        settings = self.settings.game(app.appid)
        ctx = {
            "appid": app.appid,
            "title": app.name,
            "exe_found": bool(scan.exe_path),
            "architecture": scan.architecture,
            "api": scan.api,
            "api_confidence": scan.api_confidence,
            "api_source": scan.api_source,
            "hook": scan.hook,
            "engine": engine,
            "anti_cheat": scan.anti_cheat,
            "linux_build": scan.linux_build,
            "native_hdr": wiki.get("native_hdr", "unknown"),
            "pcgw_page": wiki.get("page_name", ""),
            "pcgw_url": PCGamingWiki.page_url(wiki.get("page_name", "")),
            "pcgw_error": wiki.get("error", ""),
            "renodx_match": match,
            "renodx_error": renodx_error,
            "renodx_warnings": fixes.get("warnings", []),
            "specialk_local_gate": self.compat.specialk_local_gate(app.appid, translated=installers.can_translate(scan.api, scan.architecture)),
            "dx_translation": installers.can_translate(scan.api, scan.architecture),
            "specialk_verified": bool(settings.get("specialk_verified")),
            "specialk_wiki": bool(wiki.get("special_k")),
            "specialk_compat": bool(self.compat.tool(app.appid, "special_k")),
            "specialk_avoid_hdr": self.compat.specialk_avoid_hdr(app.appid),
            "screen": self.screen(),
            "notes": scan.notes,
        }
        return ctx

    def game_state(self, appid: str) -> dict[str, Any]:
        app = self._app(appid)
        scan = self._scan(app)
        ctx = self._context(app, scan)
        recs, options = recommend.evaluate(ctx)
        for rec in recs:
            rec.update({key: value for key, value in self._metadata(app.appid, rec["method"], ctx).items() if value})
        match = ctx.get("renodx_match") or {}
        return _ok(
            appid=app.appid,
            title=app.name,
            kind=app.kind,
            launch_options_hint=app.launch_options if app.is_shortcut else None,
            install_path=str(app.install_path),
            exe_path=scan.exe_path,
            target_dir=scan.target_dir,
            exe_override=bool(self.settings.game(appid).get("exe")),
            exe_candidates=[{"path": c.path, "label": os.path.relpath(c.path, app.install_path), "arch": c.arch} for c in scan.candidates[:10]],
            context={
                **{key: value for key, value in ctx.items() if key not in {"renodx_match", "renodx_warnings", "specialk_local_gate"}},
                "renodx_match": {key: match.get(key) for key in ("name", "status", "match_type", "addon_url", "manual_url", "bitness", "notes")} if match else None,
            },
            recommendations=recs,
            method_options=options,
            install=self.install_status(appid, app),
            user_result=self._user_result(appid),
        )

    def _metadata(self, appid: str, method: str, ctx: dict[str, Any]) -> dict[str, list[str]]:
        """Warnings, steps and notes to show for a method."""
        if method != "renodx":
            return self.compat.metadata(appid, method)
        match = ctx.get("renodx_match") or {}
        notes = [note.replace("`", "") for note in match.get("notes") or []]
        return {"warnings": list(ctx.get("renodx_warnings") or []), "manual_steps": [], "wiki_notes": notes}

    def _user_result(self, appid: str) -> str:
        """'worked'/'failed' for the currently installed method, else ''."""
        saved = self.settings.game(appid).get("result") or {}
        record = self.store.get(appid)
        if not isinstance(saved, dict) or not record or saved.get("method") != record.get("method"):
            return ""
        return str(saved.get("result") or "")

    # ------------------------------------------------------------ status
    def install_status(self, appid: str, app: SteamApp | None = None) -> dict[str, Any]:
        record = self.store.get(appid)
        if record:
            missing = [path for path in record.get("created", []) if not os.path.lexists(path)]
            older = record.get("plugin_version") != self.version
            app = app or self.steam.app(appid)
            game_updated = bool(app and app.buildid and record.get("buildid") and app.buildid != record["buildid"])
            spec = record.get("launch") or {}
            launch_outdated = bool(spec.get("env")) and spec.get("env") != launch.spec("")["env"]
            installed_reshade = str((record.get("extra") or {}).get("reshade_version") or "")
            reshade_outdated = bool(installed_reshade) and installed_reshade != ".".join(map(str, RESHADE_VERSION))
            # 0.2.0-0.4.5 hooked Unity games through opengl32 (see detect.scan_game); ReShade never saw a frame.
            wrong_hook = record.get("dll") == "opengl32" and (Path(record.get("target_dir") or "") / "UnityPlayer.dll").is_file()
            # The game had never run, so its Engine.ini couldn't be written; it can once the prefix exists.
            engine_ini_ready = bool((record.get("extra") or {}).get("engine_ini_pending")) and app is not None and (
                app.compatdata / "pfx" / "drive_c" / "users" / "steamuser" / "AppData" / "Local").is_dir()
            # 0.5.0 took the Deck OLED's EDID peak (~604 nits) instead of gamescope's profile (1000).
            wrong_peak = (record.get("plugin_version") == "0.5.0" and record["method"] in {"renodx", "special_k"}
                          and self.settings.get("auto_brightness", True) is not False
                          and self.screen().get("source") == "gamescope panel profile")
            message = f"{METHOD_LABELS.get(record['method'], record['method'])} is installed."
            if missing:
                message += f" {len(missing)} installed file(s) are missing; the game was probably updated or verified."
            elif game_updated:
                message += " The game was updated since; repair if HDR stopped working."
            elif engine_ini_ready:
                message += " The game has run once now; repair to add the Engine.ini HDR settings it needs."
            elif wrong_peak:
                message += " It was set up with the wrong peak brightness for this screen; repair to fix it."
            elif wrong_hook:
                message += " It hooks OpenGL, but this Unity game renders with Direct3D; repair to fix it."
            elif launch_outdated:
                message += " It uses older launch options; repair to update them."
            elif reshade_outdated:
                message += f" It uses ReShade {installed_reshade}; repair to switch to {'.'.join(map(str, RESHADE_VERSION))}."
            return {
                "installed": True,
                "method": record["method"],
                "dll": record.get("dll", ""),
                "target_dir": record.get("target_dir", ""),
                "installed_at": record.get("installed_at", ""),
                "installed_version": record.get("plugin_version", ""),
                "outdated": older,
                "files_ok": not missing,
                "missing": missing[:5],
                "game_updated": game_updated,
                "launch_outdated": launch_outdated,
                "needs_repair": bool(missing or launch_outdated or reshade_outdated or wrong_hook or wrong_peak or engine_ini_ready),
                "launch": record.get("launch"),
                "extra": record.get("extra", {}),
                "legacy": False,
                "message": message,
            }
        app = app or self.steam.app(appid)
        legacy = state.find_legacy(self.paths, appid, app.install_path if app else None)
        if legacy:
            return {
                "installed": True,
                "legacy": True,
                "method": legacy.get("method") or "unknown",
                "files_ok": True,
                "launch": None,
                "message": "Installed by an older Decky RenoDX version. Reinstall or remove it to switch to the new, reversible install format.",
            }
        return {"installed": False, "legacy": False, "launch": None, "message": "No HDR injection installed."}

    # ------------------------------------------------------------ install / remove
    def install(self, appid: str, method: str) -> dict[str, Any]:
        method = METHOD_ALIASES.get((method or "recommended").strip().lower(), (method or "recommended").strip().lower())
        if method not in {"recommended", "renodx", "special_k", "reshade", "native_hdr", "sdr"}:
            return _err(f"Unknown HDR method: {method}")
        if method in {"sdr", "native_hdr"}:
            result = self.uninstall(appid)
            if method == "native_hdr" and result["status"] == "success":
                # No injection, but Proton/DXVK still need the HDR switches for the game's own HDR.
                # A file-less record makes it removable and lets a later method replace the switches.
                app = self._app(appid)
                spec = launch.spec("")
                self.store.put(appid, {
                    "appid": app.appid, "title": app.name, "kind": app.kind, "buildid": app.buildid, "method": "native_hdr",
                    "dll": "", "exe_path": "", "target_dir": "", "install_path": str(app.install_path),
                    "plugin_version": self.version, "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "launch": spec, "extra": {}, "created": [], "replaced": {}, "artifacts": [],
                })
                result["launch"], result["previous_launch"] = spec, result.get("launch")
                result["message"] = (result.get("message", "") + " HDR launch switches applied; enable HDR in the game's settings.").strip()
            else:
                result["previous_launch"], result["launch"] = result.get("launch"), None
            return {**result, "method": method}
        app = self._app(appid)
        scan = self._scan(app)
        ctx = self._context(app, scan)
        recs, options = recommend.evaluate(ctx)
        if method == "recommended":
            plan = recommend.install_plan(recs)
            if not plan:
                top = recs[0]["method"] if recs else "sdr"
                result = self.install(appid, "native_hdr" if top == "native_hdr" else "sdr")
                return {**result, "message": f"{recs[0]['reason']} {result.get('message', '')}".strip() if recs else result.get("message", "")}
        else:
            option = next((item for item in options if item["method"] == method), None)
            if option and not option["available"]:
                return _err(f"{option['label']} is not available: {option['reason']}", method=method)
            plan = [method]
        return self._install_plan(app, scan, ctx, plan, explicit=method != "recommended")

    def import_renodx(self, appid: str, file_path: str) -> dict[str, Any]:
        app = self._app(appid)
        source = Path(file_path or "")
        if not source.is_file() or not fsutil.is_within(source, self.paths.home):
            return _err("Pick a downloaded RenoDX file from your home folder first.")
        if source.suffix.lower() not in IMPORT_EXTENSIONS:
            return _err("Only .addon64, .addon32, .zip, .7z and .rar files can be imported.")
        scan = self._scan(app)
        addon = self._addon_from_import(app.appid, source, scan.architecture)
        mod = {"name": addon.name, "match_type": "manual", "source_type": "manual_import", "bitness": "32" if addon.suffix.lower() == ".addon32" else "64", "imported_from": str(source)}
        ctx = self._context(app, scan)
        if ctx["anti_cheat"]:
            return _err(f"Anti-cheat detected ({', '.join(ctx['anti_cheat'])}); injection is blocked.")
        return self._install_plan(app, scan, ctx, ["renodx"], explicit=True, renodx_file=addon, renodx_mod=mod)

    def _addon_from_import(self, appid: str, source: Path, arch: str) -> Path:
        if source.suffix.lower() in {".addon64", ".addon32"}:
            return source
        # One extraction folder per game; per-game locks keep imports for a game sequential.
        target = self.paths.imports / "extracted" / appid
        if target.exists():
            shutil.rmtree(target)
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                fsutil.safe_extract_zip(archive, target)
        else:
            self.runtime.extract(source, target)
        fsutil.chown_tree(self.paths.imports)
        addons = sorted(path for path in target.rglob("*") if path.is_file() and path.suffix.lower() in {".addon64", ".addon32"})
        if not addons:
            raise ServiceError(f"No .addon64/.addon32 file was found inside {source.name}.")
        preferred = [path for path in addons if path.suffix.lower() == f".addon{arch}"]
        return (preferred or addons)[0]

    def _target(self, app: SteamApp, scan: detect.GameScan) -> Target:
        if not scan.exe_path:
            raise ServiceError("The game's Windows executable was not found." + (" It looks like a native Linux build; force Proton first." if scan.linux_build else ""))
        return Target(
            appid=app.appid, title=app.name, exe_path=Path(scan.exe_path), install_path=app.install_path,
            compatdata=app.compatdata, arch=scan.architecture, api=scan.api, hook=scan.hook or "dxgi",
        )

    def _install_plan(
        self, app: SteamApp, scan: detect.GameScan, ctx: dict[str, Any], plan: list[str], *,
        explicit: bool, renodx_file: Path | None = None, renodx_mod: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger = log.game(app.appid)
        target = self._target(app, scan)
        logger.info("Install plan for %s: %s (exe %s, api %s, arch %s)", app.name, plan, target.exe_path, target.api, target.arch)

        self.recover(app.appid)
        old_record = self.store.get(app.appid)
        current: dict[str, Any] = {"tx": None}

        def journal() -> None:
            self.store.put_pending(app.appid, {
                "stash": stash.state() if stash else None,
                "tx": current["tx"].to_record() if current["tx"] else None,
            })

        # The RenoDX tab saves its settings in ReShade.ini; read them before the old install goes.
        previous_ini = Path((old_record or {}).get("target_dir") or target.dir) / "ReShade.ini"
        try:
            ctx["previous_reshade_ini"] = previous_ini.read_text(encoding="utf-8", errors="replace") if previous_ini.is_file() else ""
        except OSError:
            ctx["previous_reshade_ini"] = ""
        # What this plugin wrote automatically last time isn't the user's choice. 0.5.0 didn't
        # record it; it wrote the screen's raw EDID peak.
        previous_auto = ((old_record or {}).get("extra") or {}).get("auto_ini")
        if previous_auto is None and (old_record or {}).get("plugin_version") == "0.5.0":
            edid = (ctx.get("screen") or {}).get("edid_peak_nits")
            previous_auto = installers.renodx_screen_settings(edid) if edid else {}
        ctx["renodx_keep"] = installers.merge_sections(
            self.settings.game(app.appid).get("renodx_settings") or {},  # saved when HDR "worked"
            installers.carried_settings(ctx["previous_reshade_ini"], previous_auto),
        )
        stash = transaction.Stash(old_record, logger, on_change=journal) if old_record else None
        journal()
        if stash:
            stash.take_apart()
        legacy = state.find_legacy(self.paths, app.appid, app.install_path)
        if legacy:
            removed, legacy_errors = state.remove_legacy(legacy, app.install_path, logger)
            logger.info("Removed %d legacy file(s) before installing; errors: %s", len(removed), legacy_errors)

        errors: list[str] = []
        manual: dict[str, Any] | None = None
        for method in plan:
            tx = current["tx"] = transaction.Transaction(logger, on_change=journal)
            try:
                result = self._run_installer(method, tx, target, ctx, renodx_file, renodx_mod)
            except ManualDownload as pending:
                tx.rollback()
                current["tx"] = None
                journal()
                manual = pending.mod
                errors.append(f"RenoDX: {pending}")
                continue
            except Exception as error:
                rollback_errors = tx.rollback()
                logger.exception("%s install failed", method)
                errors.append(f"{METHOD_LABELS.get(method, method)}: {error}")
                if rollback_errors:
                    errors.append(f"Rollback problems: {'; '.join(rollback_errors)}")
                current["tx"] = None
                journal()
                continue
            record = {
                "appid": app.appid,
                "title": app.name,
                "kind": app.kind,
                "buildid": app.buildid,
                "method": method,
                "dll": result.get("dll", ""),
                "exe_path": str(target.exe_path),
                "target_dir": str(target.dir),
                "install_path": str(app.install_path),
                "arch": target.arch,
                "api": target.api,
                "plugin_version": self.version,
                "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "launch": result["launch"],
                "extra": result.get("extra", {}),
                **tx.to_record(),
            }
            if method == "renodx":
                record["extra"]["source_addon"] = self._keep_addon_source(app.appid, Path(result["extra"]["addon"]))
            self.store.put(app.appid, record)
            # Committed: from here on the record describes the install, not the journal.
            self.store.delete_pending(app.appid)
            if stash:
                try:
                    stash.commit()
                except OSError as error:
                    logger.warning("Could not delete the previous install's leftovers: %s", error)
            logger.info("Installed %s: %s", method, result.get("message"))
            meta = self._metadata(app.appid, method, ctx)
            return _ok(
                method=method,
                message=result.get("message", ""),
                launch=result["launch"],
                previous_launch=old_record.get("launch") if old_record else None,
                failed_attempts=errors,
                legacy=bool(legacy),
                renodx_manual=self._manual_info(manual) if manual else None,
                **meta,
            )

        if stash:
            try:
                stash.put_back()
            except OSError as error:
                logger.exception("Restoring the previous install failed")
                errors.append(f"Restoring the previous install failed: {error}")
        self.store.delete_pending(app.appid)
        if manual and explicit:
            return {"status": "manual_required", **self._manual_info(manual), "kept_previous": bool(stash)}
        message = "; ".join(errors) or "Nothing could be installed."
        if stash:
            message += " Your previous HDR install was left in place."
        return _err(message, kept_previous=bool(stash), renodx_manual=self._manual_info(manual) if manual else None)

    def recover(self, appid: str) -> bool:
        """Undo an install that was interrupted (crash, reboot, plugin reload) before it finished."""
        pending = self.store.get_pending(appid)
        if pending is None:
            return False
        logger = log.game(appid)
        logger.warning("Found an interrupted install; undoing it.")
        if pending.get("tx"):
            errors, _remaining = transaction.revert(pending["tx"], logger)
            if errors:
                logger.error("Problems undoing the interrupted install: %s", errors)
        if pending.get("stash"):
            try:
                transaction.Stash.from_state(pending["stash"], logger).put_back()
            except OSError as error:
                logger.error("Could not restore the previous install: %s", error)
        self.store.delete_pending(appid)
        return True

    def recover_all(self) -> list[str]:
        recovered = [appid for appid in self.store.pending_appids() if self.recover(appid)]
        if recovered:
            log.plugin().warning("Undid interrupted installs for %s", ", ".join(recovered))
        return recovered

    def _keep_addon_source(self, appid: str, addon: Path) -> str:
        """Keep a copy of the installed addon so "Repair" works without the wiki or ~/Downloads."""
        target = self.paths.imports / "sources" / appid / addon.name
        try:
            fsutil.atomic_write_bytes(target, addon.read_bytes())
            return str(target)
        except OSError as error:
            log.game(appid).warning("Could not keep a copy of %s: %s", addon, error)
            return ""

    def repair(self, appid: str) -> dict[str, Any]:
        """Reinstall the current method (after a game update, a Steam file check, or a plugin update).

        Also migrates installs made by older plugin versions, reusing the installed RenoDX addon.
        """
        app = self._app(appid)
        record = self.store.get(appid)
        extra: dict[str, Any] = (record or {}).get("extra", {}) or {}
        if record:
            method = record["method"]
            candidates = [extra.get("source_addon"), extra.get("addon")]
        else:
            legacy = state.find_legacy(self.paths, appid, app.install_path)
            if not legacy:
                return _err("Nothing is installed for this game.")
            method = METHOD_ALIASES.get(str(legacy.get("method") or ""), str(legacy.get("method") or ""))
            candidates = [str(path) for directory in legacy.get("markers", []) for path in sorted(Path(directory).glob("renodx*.addon*"))]
        if method == "renodx":
            source = next((Path(str(item)) for item in candidates if item and Path(str(item)).is_file()), None)
            if source is not None:
                # Copy it out first: reinstalling removes the copy in the game folder.
                kept = Path(self._keep_addon_source(appid, source) or source)
                scan = self._scan(app)
                ctx = self._context(app, scan)
                mod = dict(extra.get("mod") or {"name": kept.name, "match_type": "manual"})
                current = ctx.get("renodx_match") or {}
                if current.get("name") == mod.get("name"):
                    # Records from before 0.6 didn't keep the wiki notes; take today's.
                    mod.update({key: current[key] for key in ("notes", "engine_bucket", "match_type") if key in current})
                return self._install_plan(app, scan, ctx, ["renodx"], explicit=True, renodx_file=kept, renodx_mod=mod)
        if method == "special_k_delayed":
            return _err("Special K Delayed is no longer supported. Remove HDR, then pick another method.")
        if method not in {"renodx", "special_k", "reshade", "native_hdr"}:
            method = "recommended"
        return self.install(appid, method)

    def set_launch_keep(self, appid: str, keep: dict[str, Any]) -> dict[str, Any]:
        """Remember launch options the user already had, so removing HDR leaves them alone."""
        record = self.store.get(appid)
        if not record or not record.get("launch"):
            return _err("Nothing is installed for this game.")
        clean = {
            "args": [str(item) for item in keep.get("args", []) if isinstance(item, str)][:20],
            "dlls": [str(item).lower() for item in keep.get("dlls", []) if isinstance(item, str)][:20],
            "env": [str(item) for item in keep.get("env", []) if isinstance(item, str)][:20],
        }
        record["launch"]["keep"] = clean
        self.store.put(appid, record)
        return _ok(message="Saved.")

    def display_status(self) -> dict[str, Any]:
        return _ok(**display.hdr_status(self.paths.user, self.paths.home))

    def _manual_info(self, mod: dict[str, Any]) -> dict[str, Any]:
        url = mod.get("manual_url") or next(iter(mod.get("page_links") or []), "")
        return {
            "manual_download": True,
            "url": url,
            "mod_name": mod.get("name", ""),
            "message": f"{mod.get('name', 'This RenoDX mod')} must be downloaded manually. Download it to ~/Downloads, then import it here.",
        }

    def _run_installer(
        self, method: str, tx: transaction.Transaction, target: Target, ctx: dict[str, Any],
        renodx_file: Path | None, renodx_mod: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method == "renodx":
            mod = renodx_mod or ctx.get("renodx_match")
            if not mod:
                raise InstallError("No RenoDX mod matched this game.")
            addon = renodx_file or self._download_addon(mod, target)
            target = self._rhi_hook(target)
            auto, notes, engine_ini = self._renodx_auto(target, ctx, mod, addon)
            return installers.install_renodx(tx, target, self.runtime, self.compat, addon, mod,
                                             auto=auto, keep=ctx.get("renodx_keep"), notes=notes, engine_ini=engine_ini,
                                             engine_ini_dirs=self.rhi.game(target.title).get("engine_ini_dirs"))
        if method == "special_k":
            return installers.install_specialk(tx, target, self.runtime, self.compat, peak_nits=self._peak_nits(ctx))
        if method == "reshade":
            return installers.install_reshade(tx, self._rhi_hook(target), self.runtime, self.compat, keep=ctx.get("renodx_keep"))
        raise InstallError(f"Unhandled method {method}")

    def _rhi_hook(self, target: Target) -> Target:
        """ReShade's DLL name from RHI, for games where the detected hook doesn't load."""
        dll = self.rhi.game(target.title).get("reshade_dll")
        return replace(target, hook=dll) if dll and target.api != "vulkan" else target

    def _renodx_auto(self, target: Target, ctx: dict[str, Any], mod: dict[str, Any], addon: Path) -> tuple[dict[str, dict[str, str]], list[str], str]:
        """Settings worked out for this game and screen: brightness, the wiki's resource upgrades,
        RHI's per-game values, and whether the Unreal Engine.ini lines are needed."""
        notes: list[str] = []
        peak, sdr = self._peak_nits(ctx), self._sdr_nits()
        screen = installers.renodx_screen_settings(peak, sdr)
        if peak:
            notes.append(f"Peak brightness set to {peak:g} nits" + (f", game and UI brightness to {sdr:g} nits" if sdr else "") + " for this screen.")
        wiki_notes = list(mod.get("notes") or [])
        upgrades: dict[str, str] = {}
        engine_ini = ""
        if mod.get("match_type") == "generic_listed":
            # The generic addons carry their own tuned defaults for some games; don't override those.
            if not installers.addon_knows_game(addon, [target.exe_path.name, target.title]):
                upgrades = renodx.upgrade_settings(wiki_notes)
            if renodx.engine_bucket(str(mod.get("engine_bucket") or "")) == "unreal":
                engine_ini = renodx.needs_engine_ini(wiki_notes)
        rhi_ini = self.rhi.game(target.title).get("ini", {})
        globals_ = {k: v for k, v in {**upgrades, **rhi_ini}.items()}
        preset = {k: v for k, v in rhi_ini.items() if not k.startswith("Upgrade_")}
        if upgrades or rhi_ini:
            notes.append(f"Applied {len(upgrades) + len(rhi_ini)} RenoDX setting(s) recommended for this game.")
        auto = installers.merge_sections(screen, {"renodx": globals_}, {"renodx-preset1": preset})
        return auto, notes, engine_ini

    def _sdr_nits(self) -> float | None:
        """Steam's "SDR content brightness" (HDR settings), when auto brightness is on and Steam set it."""
        if self.settings.get("auto_brightness", True) is False:
            return None
        try:
            nits = display.hdr_status(self.paths.user, str(self.paths.home)).get("sdr_nits")
        except Exception:
            return None
        return float(nits) if nits else None

    def _peak_nits(self, ctx: dict[str, Any]) -> float | None:
        """The screen's HDR peak, unless the user turned automatic brightness off."""
        if self.settings.get("auto_brightness", True) is False:
            return None
        peak = (ctx.get("screen") or {}).get("peak_nits")
        return float(peak) if peak and 100 <= float(peak) <= 10000 else None

    def screen_status(self) -> dict[str, Any]:
        enabled = self.settings.get("auto_brightness", True) is not False
        try:
            sdr = display.hdr_status(self.paths.user, str(self.paths.home)).get("sdr_nits")
        except Exception:
            sdr = None
        return _ok(**self.screen(), sdr_nits=sdr, auto_brightness=enabled)

    def set_auto_brightness(self, enabled: bool) -> dict[str, Any]:
        self.settings.set("auto_brightness", bool(enabled))
        return self.screen_status()

    def screen(self) -> dict[str, Any]:
        try:
            return display.screen_info()
        except Exception as error:  # never let this block an install
            log.plugin().info("Could not read the screen's EDID: %s", error)
            return {"peak_nits": None}

    def _download_addon(self, mod: dict[str, Any], target: Target) -> Path:
        url = str(mod.get("addon_url") or "")
        if not url:
            raise ManualDownload(mod)
        destination = self.paths.imports / "downloads" / renodx.addon_filename(url, target.title)
        last_error: Exception | None = None
        for candidate in renodx.addon_url_candidates(url):
            try:
                return net.download(candidate, destination, min_size=1024)
            except Exception as error:
                last_error = error
        raise InstallError(f"RenoDX download failed ({url}): {last_error}")

    def uninstall(self, appid: str) -> dict[str, Any]:
        if not valid_appid(appid):
            return _err(f"Invalid AppID: {appid}")
        logger = log.game(appid)
        self.recover(appid)
        record = self.store.get(appid)
        app = self.steam.app(appid)
        errors: list[str] = []
        messages: list[str] = []
        if record:
            revert_errors, remaining = transaction.revert(record, logger)
            errors += revert_errors
            if revert_errors:
                logger.error("Uninstall problems: %s", revert_errors)
                # Keep only what is still installed, so a retry never touches restored files.
                self.store.put(appid, {**record, **remaining})
            else:
                self.store.delete(appid)
                messages.append(f"Removed {METHOD_LABELS.get(record['method'], record['method'])} and restored the game's original files.")
        legacy = state.find_legacy(self.paths, appid, app.install_path if app else None)
        if legacy and app:
            removed, legacy_errors = state.remove_legacy(legacy, app.install_path, logger)
            errors += legacy_errors
            messages.append(f"Removed {len(removed)} file(s) from an older plugin version.")
        if errors:
            return _err("Some files could not be removed: " + "; ".join(errors[:5]), launch=record.get("launch") if record else None, legacy=bool(legacy))
        # "launch" here is the spec that was removed; the frontend strips it from Steam.
        return _ok(message=" ".join(messages) or "No HDR injection was installed.", launch=record.get("launch") if record else None, legacy=bool(legacy))

    # ------------------------------------------------------------ verification
    def verify(self, appid: str) -> dict[str, Any]:
        record = self.store.get(appid)
        if not record:
            status = self.install_status(appid)
            return _err(status["message"] if status.get("legacy") else "Nothing is installed for this game.")
        if record["method"] == "native_hdr":
            return _ok(message="Native HDR only uses launch options; nothing is installed in the game folder.")
        missing = [path for path in record.get("created", []) if not os.path.lexists(path)]
        if missing:
            return _err(f"{len(missing)} installed file(s) are missing, e.g. {Path(missing[0]).name}. Reinstall to repair.", missing=missing[:10])
        target = Path(record["target_dir"])
        method = record["method"]
        if method in {"renodx", "reshade"}:
            for name in ("ReShade.log", f"{record.get('dll', 'dxgi')}.log"):
                path = target / name
                if path.exists():
                    text = path.read_text(encoding="utf-8", errors="replace")[-200_000:]
                    if method == "renodx" and "renodx" in text.lower():
                        return _ok(message=f"Files are in place and {name} shows the RenoDX addon loading.")
                    if method == "reshade" and "reshade" in text.lower():
                        return _ok(message=f"Files are in place and {name} shows ReShade loading.")
            return _ok(message="Files are in place. Launch the game once, then press Home to open ReShade and confirm.")
        if method.startswith("special_k"):
            sk_dir = Path(record.get("extra", {}).get("specialk_dir") or target)
            logs = [path for path in (sk_dir / "logs").glob("*.log")] if (sk_dir / "logs").is_dir() else []
            recent = [path for path in logs if time.time() - path.stat().st_mtime < 3600]
            if recent:
                return _ok(message=f"Files are in place and Special K wrote {recent[0].name} in the last hour.")
            return _ok(message="Files are in place. Launch the game and open Special K (Ctrl+Shift+Backspace) to confirm HDR.")
        return _ok(message="Files are in place.")

    # ------------------------------------------------------------ settings
    def set_executable(self, appid: str, path: str) -> dict[str, Any]:
        app = self._app(appid)
        if path:
            exe = Path(path)
            if not (exe.is_file() and exe.suffix.lower() == ".exe" and fsutil.is_within(exe, app.install_path)):
                return _err("That executable is not inside the game's install folder.")
        self.settings.set_game(appid, "exe", path or None)
        return _ok(message="Executable override saved." if path else "Using automatic executable detection.")

    def set_result(self, appid: str, result: str) -> dict[str, Any]:
        """Remember whether the user saw HDR working with the installed method."""
        if result not in {"worked", "failed", ""}:
            return _err(f"Unknown result: {result}")
        self._app(appid)
        record = self.store.get(appid)
        method = record["method"] if record else ""
        self.settings.set_game(appid, "result", {"method": method, "result": result} if result else None)
        if method.startswith("special_k") and result:
            self.settings.set_game(appid, "specialk_verified", result == "worked")
        if method in {"renodx", "reshade"} and result == "worked":
            # Keep the RenoDX settings that worked, so a later reinstall starts from them.
            ini = Path(record.get("target_dir") or "") / "ReShade.ini"
            try:
                # Only what the user set: automatic values (brightness for the current screen) are redone each install.
                saved = installers.carried_settings(ini.read_text(encoding="utf-8", errors="replace"),
                                                    (record.get("extra") or {}).get("auto_ini")) if ini.is_file() else {}
            except OSError:
                saved = {}
            if saved:
                self.settings.set_game(appid, "renodx_settings", saved)
        return _ok(message="Thanks! Noted." if result == "worked" else "Noted." if result else "Cleared.")

    def set_specialk_verified(self, appid: str, verified: bool) -> dict[str, Any]:
        self._app(appid)
        self.settings.set_game(appid, "specialk_verified", bool(verified))
        return _ok(message="Special K HDR marked as working." if verified else "Special K HDR mark cleared.")

    # ------------------------------------------------------------ repair tools
    def reset_prefix(self, appid: str) -> dict[str, Any]:
        app = self._app(appid)
        if appid in running_appids():
            return _err("Quit the game before resetting its Proton prefix.")
        prefix = app.compatdata
        if prefix.name != appid or prefix.parent.name != "compatdata":
            return _err(f"Refusing to delete unexpected path {prefix}.")
        if not prefix.exists():
            return _ok(message="This game has no Proton prefix yet.")
        shutil.rmtree(prefix)
        record = self.store.get(appid)
        note, removed_launch = "", None
        if record and record.get("method") == "special_k_delayed":
            self.store.delete(appid)
            removed_launch = record.get("launch")
            note = " Special K Delayed lived in the prefix and was removed with it."
        return _ok(message=f"Deleted {prefix}. Steam rebuilds it on the next launch.{note}", launch=removed_launch)

    def reset_caches(self) -> dict[str, Any]:
        self.renodx.clear()
        self.rhi.clear()
        self.renodx_index.clear()
        self.pcgw.clear()
        return _ok(message="Caches cleared. Detection and mod lists will be fetched again.")

    def recent_downloads(self) -> dict[str, Any]:
        folders = [self.paths.home / "Downloads", self.paths.home / "downloads", self.paths.imports]
        now = time.time()
        files = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for item in folder.iterdir():
                suffix = item.suffix.lower()
                if not item.is_file() or suffix not in IMPORT_EXTENSIONS:
                    continue
                stat = item.stat()
                named = any(word in item.name.lower() for word in ("renodx", "hdr", "luma", "addon"))
                if suffix.startswith(".addon") or named or now - stat.st_mtime < 3 * 86400:
                    files.append({"path": str(item), "name": item.name, "size": stat.st_size, "modified": stat.st_mtime})
        files.sort(key=lambda entry: entry["modified"], reverse=True)
        return _ok(files=files[:30])

    def logs(self, appid: str) -> dict[str, Any]:
        path = log.game_log_path(appid)
        plugin_log = path.read_text(encoding="utf-8", errors="replace")[-120_000:] if path and path.exists() else ""
        proton = self.paths.home / f"steam-{appid}.log"
        proton_log = proton.read_text(encoding="utf-8", errors="replace")[-150_000:] if proton.exists() else ""
        reshade_path, reshade_log = self._reshade_log(appid)
        return _ok(plugin_log=plugin_log, proton_log=proton_log, path=str(path or ""), proton_log_path=str(proton),
                   reshade_log=reshade_log, reshade_log_path=reshade_path, checks=self._diagnose(appid, reshade_log))

    def _reshade_log(self, appid: str) -> tuple[str, str]:
        record = self.store.get(appid) or {}
        target = Path(record.get("target_dir") or "")
        if not record.get("target_dir") or record.get("method") not in {"renodx", "reshade"}:
            return "", ""
        path = target / "ReShade.log"
        text = path.read_text(encoding="utf-8", errors="replace")[-60_000:] if path.is_file() else ""
        return str(path), text

    def _diagnose(self, appid: str, reshade_log: str) -> list[str]:
        """Plain-language checks for "I installed HDR but nothing changed"."""
        record = self.store.get(appid)
        if not record:
            return ["Nothing is installed for this game."]
        checks = [f"Installed: {METHOD_LABELS.get(record['method'], record['method'])} in {record.get('target_dir') or 'launch options only'}."]
        missing = [p for p in record.get("created", []) if not os.path.lexists(p)]
        checks.append(f"✗ {len(missing)} installed file(s) are missing (game updated or verified?); use Repair." if missing else "✓ All installed files are present.")
        try:
            status = self.display_status()
        except Exception:  # xprop missing or no display: just skip this check
            status = {}
        if status.get("enabled") is False:
            checks.append("✗ HDR is turned off in SteamOS (Settings → Display). Games can't output HDR until it's on.")
        elif status.get("enabled"):
            checks.append("✓ HDR is on in SteamOS.")
        if record["method"] in {"renodx", "reshade"}:
            proxy = Path(record.get("target_dir") or "") / f"{record.get('dll') or 'dxgi'}.dll"
            size = proxy.stat().st_size if proxy.is_file() else 0
            version = (record.get("extra") or {}).get("reshade_version") or "?"
            checks.append(f"{'✓' if size > 1_000_000 else '✗'} ReShade {version} as {proxy.name}: "
                          + (f"{size // 1024} KB" if size else "missing"))
            if not reshade_log:
                checks.append("✗ ReShade.log doesn't exist yet: ReShade has never run in this game. Launch it from Steam once. "
                              "If it still doesn't appear, Steam didn't apply the launch options (check they contain WINEDLLOVERRIDES with "
                              f"{record.get('dll') or 'dxgi'}=n,b) or the game runs a different .exe (Advanced → Game executable).")
            else:
                lines = reshade_log.splitlines()
                addon_lines = [line.strip() for line in lines if "add-on" in line.lower() or "addon" in line.lower()]
                errors = [line.strip() for line in lines if re.search(r"\| ERROR \||failed", line, re.I)]
                checks.append("✓ ReShade ran in this game (ReShade.log exists).")
                if record["method"] == "renodx":
                    loaded = [line for line in addon_lines if "renodx" in line.lower()]
                    checks.append(f"{'✓' if loaded else '✗'} RenoDX add-on {'mentioned' if loaded else 'not mentioned'} in ReShade.log"
                                  + (f": {loaded[-1][-160:]}" if loaded else ". The add-on may not have loaded; see the ReShade tab."))
                checks += [f"ReShade error: {line[-200:]}" for line in errors[-5:]]
        return checks

    def runtime_status(self) -> dict[str, Any]:
        return _ok(**self.runtime.status())

    def remove_runtime(self) -> dict[str, Any]:
        self.runtime.remove()
        return _ok(message="Shared downloads removed. Installed games keep their own copies; downloads return when needed.")
