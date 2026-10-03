"""Read PE headers and the import table without loading whole binaries."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MACHINE_ARCH = {0x8664: "64", 0xAA64: "arm64", 0x14C: "32"}


@dataclass
class PEInfo:
    arch: str = "unknown"
    imports: set[str] = field(default_factory=set)


def read_pe(path: Path) -> PEInfo | None:
    try:
        with open(path, "rb") as handle:
            return _parse(handle)
    except (OSError, struct.error, ValueError):
        return None


def _read_at(handle, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) < size:
        raise ValueError("truncated PE")
    return data


def _parse(handle) -> PEInfo | None:
    dos = handle.read(64)
    if len(dos) < 64 or dos[:2] != b"MZ":
        return None
    pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
    if pe_offset > 16 * 1024 * 1024:
        return None
    header = _read_at(handle, pe_offset, 24)
    if header[:4] != b"PE\0\0":
        return None
    machine, section_count = struct.unpack_from("<HH", header, 4)
    optional_size = struct.unpack_from("<H", header, 20)[0]
    info = PEInfo(arch=MACHINE_ARCH.get(machine, "unknown"))
    optional = _read_at(handle, pe_offset + 24, optional_size)
    magic = struct.unpack_from("<H", optional, 0)[0]
    dir_offset = {0x20B: 112, 0x10B: 96}.get(magic)
    if dir_offset is None or len(optional) < dir_offset + 16:
        return info
    import_rva, import_size = struct.unpack_from("<II", optional, dir_offset + 8)
    if not import_rva or not import_size:
        return info

    table = _read_at(handle, pe_offset + 24 + optional_size, section_count * 40)
    sections = []
    for index in range(min(section_count, 96)):
        virtual_size, virtual_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", table, index * 40 + 8)
        sections.append((virtual_addr, max(virtual_size, raw_size), raw_ptr, raw_size))

    def to_offset(rva: int) -> int | None:
        for virtual_addr, virtual_size, raw_ptr, raw_size in sections:
            if virtual_addr <= rva < virtual_addr + virtual_size and rva - virtual_addr < raw_size:
                return raw_ptr + rva - virtual_addr
        return None

    descriptor = to_offset(import_rva)
    if descriptor is None:
        return info
    for index in range(1024):
        try:
            entry = _read_at(handle, descriptor + index * 20, 20)
        except ValueError:
            break
        original_thunk, _stamp, _forwarder, name_rva, first_thunk = struct.unpack("<IIIII", entry)
        if not (original_thunk or name_rva or first_thunk):
            break
        name_offset = to_offset(name_rva)
        if name_offset is None:
            continue
        handle.seek(name_offset)
        raw = handle.read(260)
        name = raw.split(b"\0", 1)[0].decode("ascii", "ignore").lower()
        if name:
            info.imports.add(name)
    return info
