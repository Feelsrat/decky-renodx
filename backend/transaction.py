"""File transactions: every change an install makes is recorded so it can be undone exactly.

* ``created``   paths that did not exist before the install (removed on uninstall)
* ``replaced``  original files we overwrote, moved to a backup first (restored on uninstall)
* ``artifacts`` names tools create at runtime (logs, caches) that did not exist before;
                removed on uninstall if present

Backups and the uninstall stash live in a ``.decky-renodx`` folder next to the
file, so moves are same-filesystem renames even for games on an SD card.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from . import fsutil
from .config import STATE_DIR_NAME


def _state_dir(path: Path) -> Path:
    return path.parent / STATE_DIR_NAME


def _unique(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.name}.{index}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise OSError(f"No free backup name for {path}")


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _move(source: Path, target: Path) -> None:
    fsutil.makedirs(target.parent)
    shutil.move(str(source), str(target))


def _prune_state_dir(directory: Path) -> None:
    """Remove an empty .decky-renodx folder and its empty backup/ folder. Stashes are never touched."""
    if directory.name != STATE_DIR_NAME:
        return
    for path in (directory / "backup", directory):
        try:
            path.rmdir()
        except OSError:
            pass


class Transaction:
    """Records every change *before* making it (write-ahead), via ``on_change``,
    so an install interrupted by a crash or power loss can still be undone."""

    def __init__(self, logger: logging.Logger, on_change: Callable[[], None] | None = None):
        self.log = logger
        self.on_change = on_change or (lambda: None)
        self.created: list[str] = []          # paths that did not exist before
        self.replaced: dict[str, str] = {}    # pre-existing path -> backup of the original
        self.artifacts: list[str] = []

    def _created(self, paths: list[Path]) -> None:
        if paths:
            self.created.extend(str(path) for path in paths)
            self.on_change()

    # ------------------------------------------------------------ recording
    def _inside_created(self, target: Path) -> bool:
        return any(fsutil.is_within(target, Path(item)) for item in self.created if item != str(target))

    def _prepare(self, target: Path) -> None:
        self._created(fsutil.makedirs(target.parent))
        key = str(target)
        if key in self.created or key in self.replaced or self._inside_created(target):
            # Ours already (or inside a folder we created): overwrite without a backup.
            if _exists(target):
                fsutil.remove_path(target)
            return
        if _exists(target):
            backup = _unique(_state_dir(target) / "backup" / target.name)
            self.replaced[key] = str(backup)
            self.on_change()
            _move(target, backup)
            fsutil.chown(backup.parent.parent)
            fsutil.chown(backup.parent)
            self.log.info("Backed up %s -> %s", target, backup)
        else:
            self.created.append(key)
            self.on_change()

    def mkdir(self, path: Path) -> Path:
        path = Path(path)
        self._created(fsutil.makedirs(path))
        return path

    def copy_file(self, source: Path, target: Path, mode: int = 0o644) -> Path:
        target = Path(target)
        self._prepare(target)
        shutil.copyfile(source, target)
        os.chmod(target, mode)
        fsutil.chown(target)
        return target

    def write_text(self, target: Path, text: str, mode: int = 0o644) -> Path:
        target = Path(target)
        self._prepare(target)
        target.write_text(text, encoding="utf-8")
        os.chmod(target, mode)
        fsutil.chown(target)
        return target

    def copy_tree(self, source: Path, target: Path, ignore: Any = None) -> Path:
        """Copy a directory. An existing target directory is backed up as a whole."""
        target = Path(target)
        self._prepare(target)
        shutil.copytree(source, target, symlinks=False, ignore=ignore)
        fsutil.chown_tree(target)
        return target

    def track_artifacts(self, directory: Path, names: list[str]) -> None:
        """Log files a tool writes at runtime; removed on uninstall only if they are plain files."""
        for name in names:
            path = Path(directory) / name
            if not _exists(path) and str(path) not in self.artifacts:
                self.artifacts.append(str(path))
        self.on_change()

    def to_record(self) -> dict[str, Any]:
        return {"created": list(self.created), "replaced": dict(self.replaced), "artifacts": list(self.artifacts)}

    # ------------------------------------------------------------ undo
    def rollback(self) -> list[str]:
        errors, _remaining = revert(self.to_record(), self.log)
        return errors


def revert(record: dict[str, Any], logger: logging.Logger) -> tuple[list[str], dict[str, Any]]:
    """Undo a recorded install.

    Returns (errors, remaining) where ``remaining`` lists only what still has
    to be undone, so a retry never touches what was already restored.
    """
    errors: list[str] = []
    remaining: dict[str, Any] = {"created": [], "replaced": {}, "artifacts": []}
    state_dirs: set[Path] = set()
    for item in record.get("artifacts", []):
        path = Path(item)
        if path.is_file() or path.is_symlink():
            try:
                path.unlink()
                logger.info("Removed runtime artifact %s", path)
            except OSError as error:
                errors.append(f"{path}: {error}")
                remaining["artifacts"].append(item)
    for item in reversed(record.get("created", [])):
        path = Path(item)
        if not _exists(path):
            continue
        try:
            fsutil.remove_path(path)
            logger.info("Removed %s", path)
        except OSError as error:
            errors.append(f"{path}: {error}")
            remaining["created"].insert(0, item)
    for original, backup in record.get("replaced", {}).items():
        original_path, backup_path = Path(original), Path(backup)
        state_dirs.add(_state_dir(original_path))
        if not _exists(backup_path):
            # The backup is gone (e.g. Steam reinstalled the game). Whatever is at the
            # path now is not provably ours, so leave it alone.
            logger.warning("Backup of %s is gone; leaving the current file in place", original)
            continue
        try:
            if _exists(original_path):
                fsutil.remove_path(original_path)
            _move(backup_path, original_path)
            logger.info("Restored %s", original_path)
        except OSError as error:
            errors.append(f"{original}: {error}")
            remaining["replaced"][original] = backup
    for directory in state_dirs:
        _prune_state_dir(directory)
    return errors, remaining


class Stash:
    """Temporarily takes an install apart so a new one can be tried, then commits or puts it back."""

    def __init__(self, record: dict[str, Any], logger: logging.Logger, on_change: Callable[[], None] | None = None):
        self.record = record
        self.log = logger
        self.on_change = on_change or (lambda: None)
        self.token = f"stash-{time.time_ns()}"
        self.moved: list[tuple[str, str]] = []                # (original location, stash location)
        self.restored: list[tuple[str, str, str]] = []        # (path, backup location, stash of our file)

    def state(self) -> dict[str, Any]:
        return {"record": self.record, "token": self.token, "moved": self.moved, "restored": self.restored}

    @classmethod
    def from_state(cls, data: dict[str, Any], logger: logging.Logger) -> "Stash":
        stash = cls(data.get("record") or {}, logger)
        stash.token = str(data.get("token") or stash.token)
        stash.moved = [tuple(item) for item in data.get("moved", [])]  # type: ignore[misc]
        stash.restored = [tuple(item) for item in data.get("restored", [])]  # type: ignore[misc]
        return stash

    def _stash_path(self, path: Path) -> Path:
        return _unique(_state_dir(path) / self.token / path.name)

    def take_apart(self) -> None:
        artifacts = [item for item in self.record.get("artifacts", []) if Path(item).is_file()]
        created = [*artifacts, *self.record.get("created", [])]
        # Children of a created folder travel with the folder.
        top_level = [item for item in created if not any(item != other and fsutil.is_within(Path(item), Path(other)) for other in created)]
        try:
            for item in reversed(top_level):
                path = Path(item)
                if not _exists(path):
                    continue
                stash_path = self._stash_path(path)
                self.moved.append((str(path), str(stash_path)))
                self.on_change()
                _move(path, stash_path)
            for original, backup in self.record.get("replaced", {}).items():
                path, backup_path = Path(original), Path(backup)
                if not _exists(backup_path):
                    continue  # nothing provably ours to swap; leave the current file alone
                ours = str(self._stash_path(path)) if _exists(path) else ""
                self.restored.append((original, backup, ours))
                self.on_change()
                if ours:
                    _move(path, Path(ours))
                _move(backup_path, path)
        except OSError:
            self.put_back()
            raise

    def put_back(self) -> None:
        for original, backup, ours in reversed(self.restored):
            # The backup only stops existing once it was moved into place.
            if _exists(Path(original)) and not _exists(Path(backup)):
                _move(Path(original), Path(backup))
            if ours and _exists(Path(ours)):
                _move(Path(ours), Path(original))
        for original, stash_path in reversed(self.moved):
            if _exists(Path(stash_path)):
                if _exists(Path(original)):
                    fsutil.remove_path(Path(original))
                _move(Path(stash_path), Path(original))
        self._cleanup()
        self.log.info("Restored the previous install after a failed switch.")

    def commit(self) -> None:
        for _original, stash_path in self.moved:
            path = Path(stash_path)
            if _exists(path):
                fsutil.remove_path(path)
        for _original, _backup, ours in self.restored:
            if ours and _exists(Path(ours)):
                fsutil.remove_path(Path(ours))
        self._cleanup()

    def _cleanup(self) -> None:
        dirs = {Path(stash).parent for _original, stash in self.moved}
        dirs |= {Path(ours).parent for _o, _b, ours in self.restored if ours}
        dirs |= {_state_dir(Path(original)) / self.token for original, _b, _ours in self.restored}
        for directory in dirs:
            if directory.name == self.token:
                try:
                    directory.rmdir()
                except OSError:
                    pass
                _prune_state_dir(directory.parent)
