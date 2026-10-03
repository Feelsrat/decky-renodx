"""Decky RenoDX backend entry point: a thin RPC layer over backend.service."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import decky

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from backend import log, net  # noqa: E402
from backend.config import COMPAT_DB_URL, Paths, resolve_user  # noqa: E402
from backend.service import HdrService, ServiceError  # noqa: E402
from backend.updater import Updater  # noqa: E402

COMPAT_REFRESH_INTERVAL = 86400


def _plugin_version() -> str:
    try:
        return str(json.loads((PLUGIN_DIR / "package.json").read_text(encoding="utf-8")).get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


class Plugin:
    def __init__(self):
        log.set_plugin_logger(decky.logger)
        user, home = resolve_user(getattr(decky, "DECKY_USER", ""), getattr(decky, "DECKY_USER_HOME", ""))
        plugin_dir = Path(getattr(decky, "DECKY_PLUGIN_DIR", PLUGIN_DIR))
        self.version = _plugin_version()
        self.paths = Paths(home=home, user=user, plugin_dir=plugin_dir)
        self.service = HdrService(self.paths, self.version)
        self.updater = Updater(plugin_dir, self.version)
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._compat_task: asyncio.Task | None = None

    async def _main(self):
        await asyncio.to_thread(self.updater.cleanup_previous)
        self._compat_task = asyncio.create_task(self._refresh_compat_db())
        decky.logger.info("Decky RenoDX %s loaded for user %s (%s)", self.version, self.paths.user, self.paths.home)

    async def _unload(self):
        if self._compat_task:
            self._compat_task.cancel()
        log.close_all()
        decky.logger.info("Decky RenoDX unloaded")

    # ------------------------------------------------------------ plumbing
    async def _call(self, fn: Callable[..., dict], *args: Any) -> dict:
        try:
            return await asyncio.to_thread(fn, *args)
        except ServiceError as error:
            return {"status": "error", "message": str(error)}
        except Exception as error:
            decky.logger.exception("%s failed", getattr(fn, "__name__", fn))
            return {"status": "error", "message": f"Unexpected error: {error}"}

    async def _exclusive(self, appid: str, fn: Callable[..., dict], *args: Any) -> dict:
        """One change per game at a time; a second request is refused rather than queued."""
        lock = self._locks.setdefault(str(appid), asyncio.Lock())
        if lock.locked():
            return {"status": "error", "busy": True, "message": "Another change is already running for this game."}
        async with lock:
            return await self._call(fn, *args)

    async def _refresh_compat_db(self):
        while True:
            try:
                text = await asyncio.to_thread(net.fetch_text, COMPAT_DB_URL, timeout=20)
                count = await asyncio.to_thread(self.service.compat.accept_remote, text)
                decky.logger.info("Compatibility database refreshed (%d games)", count)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                decky.logger.warning("Compatibility database refresh failed: %s", error)
            await asyncio.sleep(COMPAT_REFRESH_INTERVAL)

    # ------------------------------------------------------------ games
    async def list_installed_games(self) -> dict:
        return await self._call(self.service.list_games)

    async def get_game_state(self, appid: str) -> dict:
        return await self._call(self.service.game_state, str(appid))

    async def install_hdr_method(self, appid: str, method: str = "recommended") -> dict:
        return await self._exclusive(appid, self.service.install, str(appid), method)

    async def remove_hdr(self, appid: str) -> dict:
        return await self._exclusive(appid, self.service.uninstall, str(appid))

    async def import_renodx_for_game(self, appid: str, file_path: str) -> dict:
        return await self._exclusive(appid, self.service.import_renodx, str(appid), file_path)

    async def find_recent_renodx_downloads(self) -> dict:
        return await self._call(self.service.recent_downloads)

    async def verify_hdr_installation(self, appid: str) -> dict:
        return await self._call(self.service.verify, str(appid))

    async def set_game_executable(self, appid: str, path: str = "") -> dict:
        return await self._exclusive(appid, self.service.set_executable, str(appid), path)

    async def set_special_k_verified(self, appid: str, verified: bool) -> dict:
        return await self._call(self.service.set_specialk_verified, str(appid), bool(verified))

    async def set_special_k_delay(self, appid: str, seconds: int) -> dict:
        return await self._exclusive(appid, self.service.set_specialk_delay, str(appid), int(seconds))

    async def reset_game_proton_prefix(self, appid: str) -> dict:
        return await self._exclusive(appid, self.service.reset_prefix, str(appid))

    async def get_per_game_log(self, appid: str) -> dict:
        return await self._call(self.service.logs, str(appid))

    async def get_pcgw_improvements_issues(self, appid: str) -> dict:
        return await self._call(self.service.pcgw_fixes, str(appid))

    async def reset_plugin_caches(self) -> dict:
        return await self._call(self.service.reset_caches)

    async def open_url(self, url: str) -> dict:
        """Desktop Mode fallback; Game Mode opens links through Steam's browser."""
        if not str(url).startswith("https://"):
            return {"status": "error", "message": "Only https links can be opened."}
        try:
            env = net.clean_env()
            env.update({"HOME": str(self.paths.home), "USER": self.paths.user})
            subprocess.Popen(["xdg-open", url], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return {"status": "success", "message": "Opened in the desktop browser."}
        except OSError as error:
            return {"status": "error", "message": str(error)}

    # ------------------------------------------------------------ shared runtime
    async def get_runtime_status(self) -> dict:
        return await self._call(self.service.runtime_status)

    async def remove_runtime(self) -> dict:
        async with self._global_lock:
            return await self._call(self.service.remove_runtime)

    # ------------------------------------------------------------ updates
    async def get_update_status(self) -> dict:
        return self.updater.status()

    async def check_update(self, force: bool = False) -> dict:
        return await asyncio.to_thread(self.updater.check, bool(force))

    async def install_update(self) -> dict:
        async with self._global_lock:
            return await asyncio.to_thread(self.updater.install)

    async def log_error(self, message: str) -> None:
        decky.logger.error("Frontend: %s", message)
