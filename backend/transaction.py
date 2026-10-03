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
from typing import Any

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
    """Remove empty .decky-renodx folders (deepest first)."""
    if not directory.exists() or directory.name != STATE_DIR_NAME:
        return
    for dirpath, _dirnames, _filenames in sorted(os.walk(directory), key=lambda item: -len(item[0])):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


class Transaction:
    def __init__(self, logger: logging.Logger):
        self.log = logger
        self.created: list[str] = []
        self.replaced: dict[str, str] = {}
        self.artifacts: list[str] = []

    # ------------------------------------------------------------ recording
    def _inside_created(self, target: Path) -> bool:
        return any(fsutil.is_within(target, Path(item)) for item in self.created if item != str(target))

    def _prepare(self, target: Path) -> None:
        for directory in fsutil.makedirs(target.parent):
            self.created.append(str(directory))
        if str(target) in self.created or self._inside_created(target):
            # Ours already (or inside a folder we created): overwrite without a backup.
            if _exists(target):
                fsutil.remove_path(target)
            return
        if _exists(target):
            backup = _unique(_state_dir(target) / "backup" / target.name)
            _move(target, backup)
            fsutil.chown(backup.parent)
            self.replaced[str(target)] = str(backup)
            self.log.info("Backed up %s -> %s", target, backup)
        self.created.append(str(target))

    def mkdir(self, path: Path) -> Path:
        path = Path(path)
        for directory in fsutil.makedirs(path):
            self.created.append(str(directory))
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
        for name in names:
            path = Path(directory) / name
            if not _exists(path) and str(path) not in self.artifacts:
                self.artifacts.append(str(path))

    def to_record(self) -> dict[str, Any]:
        return {"created": list(self.created), "replaced": dict(self.replaced), "artifacts": list(self.artifacts)}

    # ------------------------------------------------------------ undo
    def rollback(self) -> list[str]:
        return revert(self.to_record(), self.log)


def revert(record: dict[str, Any], logger: logging.Logger) -> list[str]:
    """Undo a recorded install. Returns errors (empty on success)."""
    errors: list[str] = []
    state_dirs: set[Path] = set()
    for item in record.get("artifacts", []):
        path = Path(item)
        if _exists(path):
            try:
                fsutil.remove_path(path)
                logger.info("Removed runtime artifact %s", path)
            except OSError as error:
                errors.append(f"{path}: {error}")
    for item in reversed(record.get("created", [])):
        path = Path(item)
        if not _exists(path):
            continue
        try:
            fsutil.remove_path(path)
            logger.info("Removed %s", path)
        except OSError as error:
            errors.append(f"{path}: {error}")
    for original, backup in record.get("replaced", {}).items():
        original_path, backup_path = Path(original), Path(backup)
        state_dirs.add(_state_dir(original_path))
        if not _exists(backup_path):
            errors.append(f"Backup for {original} is missing ({backup})")
            continue
        try:
            if _exists(original_path):
                fsutil.remove_path(original_path)
            _move(backup_path, original_path)
            logger.info("Restored %s", original_path)
        except OSError as error:
            errors.append(f"{original}: {error}")
    for directory in state_dirs:
        _prune_state_dir(directory)
    return errors


class Stash:
    """Temporarily takes an install apart so a new one can be tried, then commits or puts it back."""

    def __init__(self, record: dict[str, Any], logger: logging.Logger):
        self.record = record
        self.log = logger
        self.token = f"stash-{int(time.time() * 1000)}"
        self.moved: list[tuple[str, str]] = []      # (original location, stash location)
        self.restored: list[tuple[str, str]] = []   # (original file, its backup location)

    def take_apart(self) -> None:
        created = [*self.record.get("artifacts", []), *self.record.get("created", [])]
        # Children of a created folder travel with the folder.
        top_level = [item for item in created if not any(item != other and fsutil.is_within(Path(item), Path(other)) for other in created)]
        try:
            for item in reversed(top_level):
                path = Path(item)
                if not _exists(path):
                    continue
                stash_path = _unique(_state_dir(path) / self.token / path.name)
                _move(path, stash_path)
                self.moved.append((str(path), str(stash_path)))
            for original, backup in self.record.get("replaced", {}).items():
                if _exists(Path(backup)) and not _exists(Path(original)):
                    _move(Path(backup), Path(original))
                    self.restored.append((original, backup))
        except OSError:
            self.put_back()
            raise

    def put_back(self) -> None:
        for original, backup in reversed(self.restored):
            if _exists(Path(original)):
                _move(Path(original), Path(backup))
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
        self._cleanup()

    def _cleanup(self) -> None:
        dirs = {Path(stash).parent for _original, stash in self.moved}
        dirs |= {_state_dir(Path(original)) for original, _backup in self.restored}
        for directory in dirs:
            if directory.name == self.token:
                try:
                    directory.rmdir()
                except OSError:
                    pass
                _prune_state_dir(directory.parent)
            else:
                _prune_state_dir(directory)
