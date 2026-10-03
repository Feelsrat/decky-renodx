"""The plugin's operations. Synchronous; main.py runs them in threads behind per-game locks."""
from __future__ import annotations

import os
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import detect, fsutil, installers, launch, log, net, recommend, renodx, state, transaction
from .compat import CompatDB
from .config import Paths
from .installers import InstallError, Target
from .pcgw import PCGamingWiki
from .renodx import RenoDXCatalog
from .runtime import Runtime
from .steam import SteamApp, SteamLibrary, running_appids, valid_appid

METHOD_LABELS = {
    "renodx": "RenoDX", "special_k": "Special K", "special_k_delayed": "Special K Delayed",
    "reshade": "ReShade AutoHDR", "native_hdr": "Native HDR", "sdr": "SDR",
}
METHOD_ALIASES = {"specialk": "special_k", "reshade_autohdr": "reshade", "reshade-hdr": "reshade"}
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
        self.pcgw = PCGamingWiki(paths.cache / "pcgamingwiki.json", net.fetch_json)
        self.runtime = Runtime(paths)
        self.store = state.InstallStore(paths.installs)
        self.settings = state.Settings(paths.settings_file)

    # ------------------------------------------------------------ games
    def list_games(self) -> dict[str, Any]:
        return _ok(games=[{"appid": app.appid, "name": app.name} for app in self.steam.games()])

    def _app(self, appid: str) -> SteamApp:
        if not valid_appid(appid):
            raise ServiceError(f"Invalid AppID: {appid}")
        app = self.steam.app(appid)
        if app is None:
            raise ServiceError(f"AppID {appid} is not installed in any Steam library.")
        return app

    def _scan(self, app: SteamApp) -> detect.GameScan:
        record = self.store.get(app.appid)
        override = str(self.settings.game(app.appid).get("exe") or "")
        note = "Using the executable you selected."
        if not override and record and Path(record.get("exe_path", "")).is_file():
            override, note = record["exe_path"], "Using the executable HDR was installed for."
        exclude = set(record.get("created", [])) if record else set()
        launched = None if override else self.steam.launched_executable(app.appid, app.install_path)
        scan = detect.scan_game(app.install_path, app.name, exe_override=override, override_note=note, launched_exe=launched, exclude=exclude)
        forced = self.compat.forced_api(app.appid)
        if forced:
            scan.api, scan.api_confidence, scan.api_source = forced, "high", "compatibility_db"
            scan.hook = detect.hook_for_api(forced)
        return scan

    def _context(self, app: SteamApp, scan: detect.GameScan) -> dict[str, Any]:
        wiki = self.pcgw.game_data(app.appid)
        if scan.api == "unknown" and wiki.get("graphics_api", "unknown") != "unknown":
            scan.api, scan.api_confidence, scan.api_source = wiki["graphics_api"], "metadata", "pcgamingwiki"
            scan.hook = detect.hook_for_api(scan.api)
        engine = scan.engine if scan.engine != "unknown" else (renodx.engine_bucket(wiki.get("engine", "")) or "unknown")
        match: dict[str, Any] | None = None
        renodx_error = ""
        try:
            match = self.renodx.match(app.name, aliases=self.compat.renodx_aliases(app.appid), engine=engine, architecture=scan.architecture)
        except Exception as error:
            renodx_error = f"RenoDX mod list unavailable: {error}"
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
            "pcgw_error": wiki.get("error", ""),
            "renodx_match": match,
            "renodx_error": renodx_error,
            "specialk_local_gate": self.compat.specialk_local_gate(app.appid),
            "specialk_delayed_gate": self.compat.specialk_delayed_gate(app.appid),
            "specialk_verified": bool(settings.get("specialk_verified")),
            "specialk_wiki": bool(wiki.get("special_k")),
            "specialk_compat": bool(self.compat.tool(app.appid, "special_k")),
            "specialk_avoid_hdr": self.compat.specialk_avoid_hdr(app.appid),
            "notes": scan.notes,
        }
        return ctx

    def game_state(self, appid: str) -> dict[str, Any]:
        app = self._app(appid)
        scan = self._scan(app)
        ctx = self._context(app, scan)
        recs, options = recommend.evaluate(ctx)
        for rec in recs:
            rec.update({key: value for key, value in self.compat.metadata(appid, rec["method"]).items() if value})
        match = ctx.get("renodx_match") or {}
        return _ok(
            appid=app.appid,
            title=app.name,
            install_path=str(app.install_path),
            exe_path=scan.exe_path,
            target_dir=scan.target_dir,
            exe_override=bool(self.settings.game(appid).get("exe")),
            exe_candidates=[{"path": c.path, "label": os.path.relpath(c.path, app.install_path), "arch": c.arch} for c in scan.candidates[:10]],
            context={
                **{key: value for key, value in ctx.items() if key not in {"renodx_match", "specialk_local_gate", "specialk_delayed_gate"}},
                "renodx_match": {key: match.get(key) for key in ("name", "status", "match_type", "addon_url", "manual_url", "bitness", "notes")} if match else None,
            },
            recommendations=recs,
            method_options=options,
            install=self.install_status(appid, app),
        )

    # ------------------------------------------------------------ status
    def install_status(self, appid: str, app: SteamApp | None = None) -> dict[str, Any]:
        record = self.store.get(appid)
        if record:
            missing = [path for path in record.get("created", []) if not os.path.lexists(path)]
            older = record.get("plugin_version") != self.version
            message = f"{METHOD_LABELS.get(record['method'], record['method'])} is installed."
            if missing:
                message += f" {len(missing)} installed file(s) are missing (Steam may have verified/updated the game). Reinstall to repair."
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
        if method not in {"recommended", "renodx", "special_k", "special_k_delayed", "reshade", "native_hdr", "sdr"}:
            return _err(f"Unknown HDR method: {method}")
        if method in {"sdr", "native_hdr"}:
            result = self.uninstall(appid)
            if method == "native_hdr" and result["status"] == "success":
                # No injection, but Proton/DXVK still need the HDR switches for the game's own HDR.
                result["launch"], result["previous_launch"] = launch.spec(""), result.get("launch")
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
        addon = self._addon_from_import(source, scan.architecture)
        mod = {"name": addon.name, "match_type": "manual", "source_type": "manual_import", "bitness": "32" if addon.suffix.lower() == ".addon32" else "64", "imported_from": str(source)}
        ctx = self._context(app, scan)
        if ctx["anti_cheat"]:
            return _err(f"Anti-cheat detected ({', '.join(ctx['anti_cheat'])}); injection is blocked.")
        return self._install_plan(app, scan, ctx, ["renodx"], explicit=True, renodx_file=addon, renodx_mod=mod)

    def _addon_from_import(self, source: Path, arch: str) -> Path:
        if source.suffix.lower() in {".addon64", ".addon32"}:
            return source
        target = self.paths.imports / renodx.normalize_title(source.stem)[:60]
        if target.exists():
            shutil.rmtree(target)
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                fsutil.safe_extract_zip(archive, target)
        else:
            self.runtime.extract(source, target)
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

        old_record = self.store.get(app.appid)
        stash = transaction.Stash(old_record, logger) if old_record else None
        if stash:
            stash.take_apart()
        legacy = state.find_legacy(self.paths, app.appid, app.install_path)
        if legacy:
            removed, legacy_errors = state.remove_legacy(legacy, app.install_path, logger)
            logger.info("Removed %d legacy file(s) before installing; errors: %s", len(removed), legacy_errors)

        errors: list[str] = []
        manual: dict[str, Any] | None = None
        for method in plan:
            tx = transaction.Transaction(logger)
            try:
                result = self._run_installer(method, tx, target, ctx, renodx_file, renodx_mod)
            except ManualDownload as pending:
                tx.rollback()
                manual = pending.mod
                errors.append(f"RenoDX: {pending}")
                continue
            except Exception as error:
                rollback_errors = tx.rollback()
                logger.exception("%s install failed", method)
                errors.append(f"{METHOD_LABELS.get(method, method)}: {error}")
                if rollback_errors:
                    errors.append(f"Rollback problems: {'; '.join(rollback_errors)}")
                continue
            record = {
                "appid": app.appid,
                "title": app.name,
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
            self.store.put(app.appid, record)
            if stash:
                stash.commit()
            logger.info("Installed %s: %s", method, result.get("message"))
            meta = self.compat.metadata(app.appid, method)
            return _ok(
                method=method,
                message=result.get("message", ""),
                launch=result["launch"],
                previous_launch=old_record.get("launch") if old_record else None,
                launch_options=result["launch"]["preview"],
                failed_attempts=errors,
                legacy=bool(legacy),
                renodx_manual=self._manual_info(manual) if manual else None,
                **meta,
            )

        if stash:
            stash.put_back()
        if manual and explicit:
            return {"status": "manual_required", **self._manual_info(manual), "kept_previous": bool(stash)}
        message = "; ".join(errors) or "Nothing could be installed."
        if stash:
            message += " Your previous HDR install was left in place."
        return _err(message, kept_previous=bool(stash), renodx_manual=self._manual_info(manual) if manual else None)

    def _manual_info(self, mod: dict[str, Any]) -> dict[str, Any]:
        url = mod.get("manual_url") or next(iter(mod.get("page_links") or []), "")
        return {
            "manual_download": True,
            "url": url,
            "mod_name": mod.get("name", ""),
            "message": f"{mod.get('name', 'This RenoDX mod')} must be downloaded manually. Download it to ~/Downloads, then import it here.",
        }

    def _run_installer(self, method: str, tx: transaction.Transaction, target: Target, ctx: dict[str, Any], renodx_file: Path | None, renodx_mod: dict[str, Any] | None) -> dict[str, Any]:
        if method == "renodx":
            mod = renodx_mod or ctx.get("renodx_match")
            if not mod:
                raise InstallError("No RenoDX mod matched this game.")
            addon = renodx_file or self._download_addon(mod, target)
            return installers.install_renodx(tx, target, self.runtime, self.compat, addon, mod)
        if method == "special_k":
            return installers.install_specialk(tx, target, self.runtime, self.compat)
        if method == "special_k_delayed":
            return installers.install_specialk_delayed(tx, target, self.runtime, self.compat, self._wrapper())
        if method == "reshade":
            return installers.install_reshade(tx, target, self.runtime, self.compat)
        raise InstallError(f"Unhandled method {method}")

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

    def _wrapper(self) -> Path:
        source = self.paths.assets_dir() / "specialk-delayed-launch.sh"
        if not source.is_file():
            raise InstallError("The Special K launch wrapper is missing from the plugin.")
        # Outside bin/ so "remove shared downloads" never breaks a game's launch options.
        target = self.paths.data / "launch" / "specialk-delayed-launch.sh"
        fsutil.atomic_write_bytes(target, source.read_bytes(), mode=0o755)
        return target

    def uninstall(self, appid: str) -> dict[str, Any]:
        if not valid_appid(appid):
            return _err(f"Invalid AppID: {appid}")
        logger = log.game(appid)
        record = self.store.get(appid)
        app = self.steam.app(appid)
        errors: list[str] = []
        messages: list[str] = []
        if record:
            errors += transaction.revert(record, logger)
            if errors:
                logger.error("Uninstall problems: %s", errors)
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

    def set_specialk_verified(self, appid: str, verified: bool) -> dict[str, Any]:
        self._app(appid)
        self.settings.set_game(appid, "specialk_verified", bool(verified))
        return _ok(message="Special K HDR marked as working." if verified else "Special K HDR mark cleared.")

    def set_specialk_delay(self, appid: str, seconds: int) -> dict[str, Any]:
        record = self.store.get(appid)
        if not record or record.get("method") != "special_k_delayed":
            return _err("Special K Delayed is not installed for this game.")
        seconds = max(1, min(60, int(seconds)))
        old_launch = record["launch"]
        wrapper = list(old_launch.get("wrapper", []))
        if len(wrapper) >= 4:
            wrapper[3] = str(seconds)
        new_launch = launch.spec("", args=old_launch.get("args", []), wrapper=wrapper)
        profiles = Path(record["extra"]["specialk_dir"]) / "Profiles.ini"
        if profiles.exists():
            text = profiles.read_text(encoding="utf-8", errors="replace")
            section = f"Profile.{Path(record['exe_path']).stem}"
            fsutil.atomic_write_text(profiles, installers.upsert_ini(text, section, {"GlobalInjectDelay": f"{float(seconds)}"}))
        record["launch"] = new_launch
        record["extra"]["delay"] = seconds
        self.store.put(appid, record)
        return _ok(message=f"Special K will inject {seconds}s after launch.", launch=new_launch, previous_launch=old_launch)

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
        note = ""
        if record and record.get("method") == "special_k_delayed":
            self.store.delete(appid)
            note = " Special K Delayed lived in the prefix and was removed with it."
        return _ok(message=f"Deleted {prefix}. Steam rebuilds it on the next launch.{note}")

    def reset_caches(self) -> dict[str, Any]:
        self.renodx.clear()
        self.pcgw.clear()
        meta = self.paths.runtime / "reshade" / "current.json"
        data = fsutil.read_json(meta, {}) or {}
        if data:
            data["checked_at"] = 0
            fsutil.write_json(meta, data)
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
        return _ok(plugin_log=plugin_log, proton_log=proton_log, path=str(path or ""), proton_log_path=str(proton))

    def pcgw_fixes(self, appid: str) -> dict[str, Any]:
        return self.pcgw.improvements(appid)

    def runtime_status(self) -> dict[str, Any]:
        return _ok(**self.runtime.status())

    def remove_runtime(self) -> dict[str, Any]:
        self.runtime.remove()
        return _ok(message="Shared downloads removed. Installed games keep their own copies; downloads return when needed.")
