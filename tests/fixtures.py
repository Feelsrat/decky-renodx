"""Builders for fake Steam libraries, PE binaries and a runtime that never touches the network."""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from pathlib import Path

from backend.config import Paths
from backend.runtime import Runtime


def make_pe(path: Path, *, arch: str = "64", imports: tuple[str, ...] = (), size: int = 128 * 1024, marker: bytes = b"") -> Path:
    """Write a minimal but valid PE with an import table."""
    pe32plus = arch == "64"
    optional_size = 240 if pe32plus else 224
    names = b""
    name_offsets = []
    descriptors_size = 20 * (len(imports) + 1)
    for dll in imports:
        name_offsets.append(descriptors_size + len(names))
        names += dll.encode() + b"\0"
    section_va, section_raw = 0x1000, 0x400
    descriptors = b"".join(struct.pack("<IIIII", 0, 0, 0, section_va + offset, 0) for offset in name_offsets) + b"\0" * 20
    section = descriptors + names + marker
    section_size = max(len(section), 0x200)

    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<4sHHIIIHH", b"PE\0\0", 0x8664 if pe32plus else 0x14C, 1, 0, 0, 0, optional_size, 0x22)
    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x20B if pe32plus else 0x10B)
    dir_offset = 112 if pe32plus else 96
    if imports:
        struct.pack_into("<II", optional, dir_offset + 8, section_va, descriptors_size)
    section_header = struct.pack("<8sIIIIIIHHI", b".rdata\0\0", section_size, section_va, section_size, section_raw, 0, 0, 0, 0, 0x40000040)
    header = bytes(dos) + coff + bytes(optional) + section_header
    data = header + b"\0" * (section_raw - len(header)) + section + b"\0" * (section_size - len(section))
    data += b"\0" * max(0, size - len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class FakeSteam:
    """A home folder with an internal library and an "SD card" library."""

    def __init__(self, root: Path | None = None) -> None:
        self.tmp = None if root else tempfile.TemporaryDirectory(prefix="decky-renodx-test-")
        self.root = Path(root) if root else Path(self.tmp.name)
        self.home = self.root / "home" / "deck"
        self.steam = self.home / ".local" / "share" / "Steam"
        self.sdcard = self.root / "run" / "media" / "mmcblk0p1"
        self.plugin_dir = self.root / "plugin"
        (self.steam / "steamapps").mkdir(parents=True, exist_ok=True)
        (self.sdcard / "steamapps").mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "defaults" / "assets").mkdir(parents=True, exist_ok=True)
        repo = Path(__file__).resolve().parents[1]
        (self.plugin_dir / "defaults" / "assets" / "specialk-delayed-launch.sh").write_bytes((repo / "defaults" / "assets" / "specialk-delayed-launch.sh").read_bytes())
        (self.plugin_dir / "compatibility.json").write_text('{"games": {}}', encoding="utf-8")
        (self.steam / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n'
            f'\t"0"\n\t{{\n\t\t"path"\t\t"{self.steam}"\n\t}}\n'
            f'\t"1"\n\t{{\n\t\t"path"\t\t"{self.sdcard}"\n\t}}\n}}\n',
            encoding="utf-8",
        )
        self.paths = Paths(home=self.home, user="deck", plugin_dir=self.plugin_dir)

    def add_game(self, appid: str, name: str, installdir: str, *, sdcard: bool = False) -> Path:
        library = self.sdcard if sdcard else self.steam
        (library / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"installdir"\t\t"{installdir}"\n}}\n', encoding="utf-8"
        )
        path = library / "steamapps" / "common" / installdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_shortcut(self, name: str, exe: str, start_dir: str, appid: int | None = 0x9ABCDEF0, launch_options: str = "") -> None:
        """Append a non-Steam game to userdata/1/config/shortcuts.vdf (binary KeyValues)."""
        path = self.steam / "userdata" / "1" / "config" / "shortcuts.vdf"
        entries = getattr(self, "_shortcuts", [])
        entries.append((name, exe, start_dir, appid, launch_options))
        self._shortcuts = entries

        def string(key: str, value: str) -> bytes:
            return b"\x01" + key.encode() + b"\0" + value.encode() + b"\0"

        body = b""
        for index, (n, e, d, a, lo) in enumerate(entries):
            body += b"\x00" + str(index).encode() + b"\0"
            if a is not None:
                body += b"\x02appid\0" + struct.pack("<I", a)
            body += string("AppName", n) + string("Exe", f'"{e}"') + string("StartDir", f'"{d}"') + string("LaunchOptions", lo) + b"\x08"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00shortcuts\0" + body + b"\x08\x08")

    def compatdata(self, appid: str, *, sdcard: bool = False) -> Path:
        prefix = (self.sdcard if sdcard else self.steam) / "steamapps" / "compatdata" / appid / "pfx" / "drive_c"
        prefix.mkdir(parents=True, exist_ok=True)
        return prefix.parents[1]

    def cleanup(self) -> None:
        if self.tmp:
            self.tmp.cleanup()


class OfflineRuntime(Runtime):
    """Runtime whose 'downloads' are generated locally."""

    def __init__(self, paths: Paths, fail: set[str] | None = None):
        super().__init__(paths, download=self._no_network, fetch_text=self._no_network, fetch_json=self._no_network)
        self.fail = fail or set()
        self.store = paths.runtime / "fake"

    @staticmethod
    def _no_network(*_args, **_kwargs):
        raise AssertionError("tests must not hit the network")

    def _check(self, name: str) -> None:
        if name in self.fail:
            raise RuntimeError(f"simulated {name} failure")

    def reshade(self):
        self._check("reshade")
        directory = self.store / "reshade"
        for bits in ("32", "64"):
            make_pe(directory / f"ReShade{bits}.dll", arch=bits, marker=b"ReShade by crosire reshade.me", size=4096)
        return {"version": "6.7.3", "dir": directory, "dll": {"64": directory / "ReShade64.dll", "32": directory / "ReShade32.dll"}}

    def autohdr_pack(self):
        self._check("autohdr")
        shaders = self.store / "autohdr" / "ReShade_shaders" / "Merged"
        (shaders / "Shaders" / "lilium__hdr").mkdir(parents=True, exist_ok=True)
        (shaders / "Textures").mkdir(parents=True, exist_ok=True)
        (shaders / "Shaders" / "AutoHDR.fx").write_text("// autohdr", encoding="utf-8")
        (shaders / "Shaders" / "lilium__hdr" / "lilium__tone_mapping.fx").write_text("// lilium", encoding="utf-8")
        addons = self.store / "autohdr" / "addons"
        addons.mkdir(parents=True, exist_ok=True)
        (addons / "AutoHDR64.addon").write_bytes(b"autohdr64")
        (addons / "AutoHDR32.addon").write_bytes(b"autohdr32")
        return {"shaders": self.store / "autohdr" / "ReShade_shaders", "addons": {"64": addons / "AutoHDR64.addon", "32": addons / "AutoHDR32.addon"}}

    def specialk(self):
        self._check("specialk")
        root = self.store / "SpecialK"
        make_pe(root / "SpecialK64.dll", marker=b"SpecialK", size=4096)
        make_pe(root / "SpecialK32.dll", arch="32", marker=b"SpecialK", size=4096)
        make_pe(root / "SKIF.exe", size=4096)
        return root

    def dgvoodoo(self):
        self._check("dgvoodoo")
        root = self.store / "dgVoodoo2"
        for arch, bits in (("x86", "32"), ("x64", "64")):
            make_pe(root / "MS" / arch / "D3D9.dll", arch=bits, marker=b"dgVoodoo", size=4096)
        make_pe(root / "MS" / "x86" / "D3D8.dll", arch="32", marker=b"dgVoodoo", size=4096)
        (root / "dgVoodoo.conf").write_text(
            "[General]\r\nOutputAPI                            = bestavailable\r\nFullScreenMode                       = true\r\n\r\n"
            "[Glide]\r\nResolution                          = unforced\r\n\r\n"
            "[DirectX]\r\nVRAM                                = 256\r\nResolution                          = unforced\r\n"
            "dgVoodooWatermark                   = true\r\n", encoding="utf-8")
        return root


def tree_digest(root: Path) -> dict[str, str]:
    """Relative path -> content hash (dirs map to 'dir'), for byte-identical comparisons."""
    result: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            result[os.path.relpath(os.path.join(dirpath, name), root)] = "dir"
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as handle:
                result[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return result
