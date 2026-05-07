import struct
from pathlib import Path


def imported_dlls(path: str | Path) -> set[str]:
    """Return imported DLL names from a PE file using pefile when available.

    Falls back to a tiny import-table parser so the Deck plugin does not depend
    on system pip packages being installed.
    """
    try:
        import pefile  # type: ignore

        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        return {
            entry.dll.decode("utf-8", errors="ignore").lower()
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])
            if getattr(entry, "dll", None)
        }
    except Exception:
        return _imported_dlls_minimal(Path(path))


def pe_architecture(path: str | Path) -> str:
    try:
        data = Path(path).read_bytes()[:0x1000]
        if data[:2] != b"MZ":
            return "unknown"
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset:pe_offset + 4] != b"PE\0\0":
            return "unknown"
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        if machine == 0x8664:
            return "64"
        if machine == 0x14C:
            return "32"
    except Exception:
        pass
    return "unknown"


def _imported_dlls_minimal(path: Path) -> set[str]:
    data = path.read_bytes()
    if len(data) < 0x100 or data[:2] != b"MZ":
        return set()
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        return set()

    coff = pe_offset + 4
    section_count = struct.unpack_from("<H", data, coff + 2)[0]
    optional_size = struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    data_dir = optional + (112 if magic == 0x20B else 96 if magic == 0x10B else 0)
    if not data_dir:
        return set()

    import_rva, import_size = struct.unpack_from("<II", data, data_dir + 8)
    if not import_rva or not import_size:
        return set()

    sections = []
    section_table = optional + optional_size
    for index in range(section_count):
        offset = section_table + index * 40
        if offset + 40 > len(data):
            break
        virtual_size, virtual_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((virtual_addr, max(virtual_size, raw_size), raw_ptr, raw_size))

    descriptor_offset = _rva_to_offset(import_rva, sections)
    if descriptor_offset is None:
        return set()

    dlls = set()
    for index in range(512):
        offset = descriptor_offset + index * 20
        if offset + 20 > len(data):
            break
        original_first_thunk, _time, _forwarder, name_rva, first_thunk = struct.unpack_from("<IIIII", data, offset)
        if not any([original_first_thunk, name_rva, first_thunk]):
            break
        name_offset = _rva_to_offset(name_rva, sections)
        if name_offset is None:
            continue
        end = data.find(b"\0", name_offset, min(len(data), name_offset + 260))
        if end > name_offset:
            dlls.add(data[name_offset:end].decode("ascii", errors="ignore").lower())
    return dlls


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for virtual_addr, virtual_size, raw_ptr, raw_size in sections:
        if virtual_addr <= rva < virtual_addr + virtual_size:
            delta = rva - virtual_addr
            if delta < raw_size:
                return raw_ptr + delta
    return None
