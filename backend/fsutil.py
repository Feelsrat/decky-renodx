"""Filesystem helpers: ownership, atomic writes and safe archive extraction.

The plugin runs as root (needed for self-update), but everything it writes
into the user's home or game folders must end up owned by the deck user.
Instead of ``chown -R`` over whole game folders we chown exactly the paths we
create.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    import pwd
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore[assignment]

_owner: tuple[int, int] | None = None


def set_owner(user: str) -> None:
    """Chown created paths to ``user`` when running as root."""
    global _owner
    _owner = None
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or pwd is None or not user or user == "root":
        return
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return
    _owner = (entry.pw_uid, entry.pw_gid)


def chown(path: Path | str) -> None:
    if _owner is None:
        return
    try:
        os.lchown(path, *_owner)
    except OSError:
        pass


def chown_tree(root: Path) -> None:
    """Chown a tree the plugin itself created (never a game folder)."""
    if _owner is None or not root.exists():
        return
    chown(root)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            chown(os.path.join(dirpath, name))


def makedirs(path: Path) -> list[Path]:
    """mkdir -p that returns (and chowns) every directory it created."""
    path = Path(path)
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    created: list[Path] = []
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        chown(directory)
        created.append(directory)
    return created


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    path = Path(path)
    makedirs(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        chown(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode)


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def remove_path(path: Path) -> None:
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def is_within(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
        return True
    except ValueError:
        return False


def safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target = Path(target)
    makedirs(target)
    for member in archive.infolist():
        destination = target / member.filename
        if not is_within(destination, target) or os.path.isabs(member.filename):
            raise ValueError(f"Unsafe path in archive: {member.filename}")
    archive.extractall(target)


def safe_extract_tar(archive: tarfile.TarFile, target: Path, members: list[tarfile.TarInfo] | None = None) -> None:
    """Extract regular files and directories only, refusing anything that escapes ``target``."""
    target = Path(target)
    makedirs(target)
    selected = []
    for member in members if members is not None else archive.getmembers():
        if not (member.isfile() or member.isdir()):
            continue
        if os.path.isabs(member.name) or not is_within(target / member.name, target):
            raise ValueError(f"Unsafe path in archive: {member.name}")
        selected.append(member)
    for member in selected:
        destination = target / member.name
        if member.isdir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            continue
        with source, open(destination, "wb") as handle:
            shutil.copyfileobj(source, handle)
        os.chmod(destination, 0o755 if member.mode & 0o111 else 0o644)
