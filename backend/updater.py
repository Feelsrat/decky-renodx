"""Self-update from GitHub releases.

The release zip must match the SHA-256 digest GitHub reports for the asset.
Swapping the plugin folder happens in a helper started as its own systemd
unit, so stopping Decky Loader does not kill it halfway.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import fsutil, log, net
from .config import GITHUB_RELEASES_URL, PLUGIN_NAME, PLUGIN_PACKAGE

CHECK_TTL = 6 * 3600
REQUIRED_FILES = ("plugin.json", "package.json", "main.py", "dist/index.js", "backend/service.py", "defaults/assets/specialk-delayed-launch.sh")


def parse_version(version: str) -> tuple[int, ...]:
    core = str(version or "").strip().removeprefix("v").split("-")[0]
    parts = []
    for part in core.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts) or (0,)


class Updater:
    def __init__(self, plugin_dir: Path, version: str, *, fetch_json: Callable[..., Any] = net.fetch_json, download: Callable[..., Path] = net.download):
        self.plugin_dir = Path(plugin_dir)
        self.version = version
        self._fetch_json = fetch_json
        self._download = download
        self._cached: dict[str, Any] | None = None
        self._checked_at = 0.0

    @property
    def work_dir(self) -> Path:
        """Staging and backup live outside the plugins folder, so Decky never loads them as plugins.

        It must be on the same filesystem as the plugin for the final swap to be two renames.
        """
        plugins = self.plugin_dir.resolve().parent
        if plugins.name == "plugins":
            return plugins.parent / "data" / PLUGIN_PACKAGE / "update"
        return plugins / f".{PLUGIN_PACKAGE}-update"

    @staticmethod
    def elevated() -> bool:
        return not hasattr(os, "geteuid") or os.geteuid() == 0

    def status(self) -> dict[str, Any]:
        if self._cached and time.time() - self._checked_at < CHECK_TTL:
            return {**self._cached, "current": self.version}
        return {"ok": True, "current": self.version, "elevated": self.elevated(), "hasUpdate": False, "canInstall": False, "message": "Not checked yet."}

    def latest_release(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        releases = self._fetch_json(GITHUB_RELEASES_URL, timeout=15, headers={"Accept": "application/vnd.github+json"})
        if not isinstance(releases, list):
            raise net.NetError("GitHub returned an unexpected response")
        for release in releases:
            if release.get("draft") or release.get("prerelease"):
                continue
            asset = next((a for a in release.get("assets", []) if str(a.get("name", "")) == f"{PLUGIN_PACKAGE}.zip"), None)
            if asset:
                return release, asset
        return None

    def check(self, force: bool = False) -> dict[str, Any]:
        if not force and self._cached and time.time() - self._checked_at < CHECK_TTL:
            return self._cached
        try:
            found = self.latest_release()
        except Exception as error:
            return {"ok": False, "current": self.version, "elevated": self.elevated(), "hasUpdate": False, "canInstall": False, "message": f"Could not read GitHub releases: {error}"}
        latest = str(found[0].get("tag_name", "")).removeprefix("v") if found else ""
        has_update = bool(found and parse_version(latest) > parse_version(self.version))
        result = {
            "ok": True,
            "current": self.version,
            "latest": latest,
            "elevated": self.elevated(),
            "hasUpdate": has_update,
            "canInstall": has_update and self.elevated(),
            "releaseUrl": found[0].get("html_url", "") if found else "",
            "message": "Update available." if has_update else "You have the latest version.",
        }
        self._cached, self._checked_at = result, time.time()
        return result

    def install(self) -> dict[str, Any]:
        status = self.check(force=True)
        if not status.get("canInstall"):
            return {**status, "ok": False, "message": status.get("message") if not status.get("hasUpdate") else "Root permissions are required to install updates."}
        release, asset = self.latest_release() or ({}, {})
        digest = str(asset.get("digest") or "")
        if not digest.startswith("sha256:"):
            return {**status, "ok": False, "message": "The release has no SHA-256 digest, so it cannot be verified. Update manually."}
        self.work_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=str(self.work_dir)))
        try:
            with tempfile.TemporaryDirectory(prefix=f"{PLUGIN_PACKAGE}-update-") as temp:
                archive = self._download(str(asset["browser_download_url"]), Path(temp) / "release.zip", min_size=10_000, sha256=digest.split(":", 1)[1])
                extract = Path(temp) / "x"
                with zipfile.ZipFile(archive) as handle:
                    fsutil.safe_extract_zip(handle, extract)
                plugin_root = next((path for path in [extract, *extract.iterdir()] if (path / "plugin.json").is_file()), None)
                if plugin_root is None:
                    raise ValueError("The release zip does not contain a Decky plugin.")
                validate_plugin(plugin_root)
                shutil.rmtree(staging)
                shutil.copytree(plugin_root, staging, symlinks=False)
            scheduled = self._schedule_swap(staging)
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            log.plugin().exception("Update failed")
            return {**status, "ok": False, "message": f"Update failed: {error}"}
        latest = str(release.get("tag_name", "")).removeprefix("v")
        result = {**status, "ok": True, "hasUpdate": False, "canInstall": False, "requiresRestart": True, "installedVersion": latest,
                  "message": f"Update {latest} downloaded and verified. Decky Loader restarts in a few seconds ({scheduled})."}
        self._cached = result
        return result

    def _schedule_swap(self, staging: Path) -> str:
        plugin_dir = self.plugin_dir.resolve()
        backup = self.work_dir / "previous"
        log_path = self.work_dir / "update.log"
        script = f"""#!/bin/bash
set -u
plugin={shlex.quote(str(plugin_dir))}
staging={shlex.quote(str(staging))}
backup={shlex.quote(str(backup))}
exec >>{shlex.quote(str(log_path))} 2>&1
echo "[$(date -Is)] applying {PLUGIN_PACKAGE} update"
sleep 2
systemctl stop plugin_loader.service 2>/dev/null || systemctl --user stop plugin_loader.service 2>/dev/null
sleep 1
rm -rf "$backup"
if mv "$plugin" "$backup" && mv "$staging" "$plugin"; then
  chown -R --reference="$backup" "$plugin" 2>/dev/null || true
  echo "update applied"
else
  echo "swap failed, restoring previous version"
  [ -d "$backup" ] && [ ! -e "$plugin" ] && mv "$backup" "$plugin"
  rm -rf "$staging"
fi
systemctl start plugin_loader.service 2>/dev/null || systemctl --user start plugin_loader.service 2>/dev/null
rm -f "$0"
"""
        fd, helper = tempfile.mkstemp(prefix="apply-", suffix=".sh", dir=str(self.work_dir))
        with os.fdopen(fd, "w") as handle:
            handle.write(script)
        os.chmod(helper, 0o700)
        env = net.clean_env()
        unit = f"{PLUGIN_PACKAGE}-update-{int(time.time())}"
        if shutil.which("systemd-run"):
            result = subprocess.run(["systemd-run", f"--unit={unit}", "--collect", "--no-block", "/bin/bash", helper], capture_output=True, text=True, timeout=15, env=env)
            if result.returncode == 0:
                return f"systemd unit {unit}"
            log.plugin().warning("systemd-run failed: %s", result.stderr.strip())
        subprocess.Popen(["/bin/bash", helper], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        return "detached helper"

    def cleanup_previous(self) -> None:
        legacy_backup = self.plugin_dir.with_name(f"{self.plugin_dir.name}.previous")
        leftovers = [legacy_backup, *self.plugin_dir.parent.glob(f".{self.plugin_dir.name}.update-*")]
        if self.work_dir.is_dir():
            leftovers += [path for path in self.work_dir.iterdir() if path.name == "previous" or path.name.startswith("staging-")]
        for path in leftovers:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
            except OSError as error:
                log.plugin().warning("Could not remove old update folder %s: %s", path, error)


def validate_plugin(root: Path) -> None:
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            raise ValueError(f"The release zip is missing {name}.")
    plugin = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    if plugin.get("name") != PLUGIN_NAME or package.get("name") != PLUGIN_PACKAGE or not package.get("version"):
        raise ValueError("The release zip is not a Decky RenoDX build.")
