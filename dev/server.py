#!/usr/bin/env python3
"""Local dev server: the real plugin backend against a fake Steam library, plus the UI harness.

    pnpm dev                      # build the harness and serve http://localhost:8787
    python3 dev/server.py --hdr off   # pretend the Deck's HDR switch is off
    python3 dev/server.py --reset     # start over with a fresh fake library

Nothing touches the network or your real Steam install: downloads are faked and
the library lives in dev/.sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import threading
import time
import types
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "dev"
SANDBOX = DEV / ".sandbox"
sys.path.insert(0, str(ROOT))

from tests.fixtures import FakeSteam, OfflineRuntime, make_pe  # noqa: E402

WIKI = """
# List
| Name | Maintainer | Links | Status |
| --- | --- | --- | --- |
| Shippy | Dev | [![Snapshot](badge)](https://example.com/renodx-shippy.addon64) | :white_check_mark: [ⓘ](# "`R10G10B10A2_UNORM` `Output Size`. Disable in-game HDR.") |
| Retro Racer | Dev | [![Snapshot](badge)](https://example.com/renodx-retroracer.addon32) | :construction: |
| Manual Game | Dev | [![Nexus Mods](badge)](https://www.nexusmods.com/game/mods/1) | :white_check_mark: |
"""

PCGW = {
    "7000": {"native_hdr": "true", "graphics_api": "d3d12", "page_name": "Bright Lights"},
    "9000": {"engine": "Unreal Engine 5"},
}

# Stand-in for RHI's manifest (backend/rhi.py).
RHI = {"wikiNameOverrides": {}, "installWarnings": {"Shippy": {"renodx": "DLSS sharpening must be turned off each session."}}}

COMPAT = {"games": {
    "8000": {"name": "Old Classic", "tools": {"special_k": {
        "automation": {"warnings": ["The launcher must be skipped with -nolauncher."]},
        "launch_options": ["-nolauncher"],
    }}},
}}


def build_library(fake: FakeSteam) -> None:
    def game(appid: str, name: str, folder: str, exe: str, imports: tuple[str, ...], arch: str = "64", **kwargs) -> Path:
        root = fake.add_game(appid, name, folder, **kwargs)
        make_pe(root / exe, arch=arch, imports=imports, size=300 * 1024)
        return root

    root = fake.add_game("1000", "Shippy", "Shippy")
    make_pe(root / "Shippy.exe", size=150 * 1024)
    make_pe(root / "Shippy" / "Binaries" / "Win64" / "Shippy-Win64-Shipping.exe", imports=("d3d12.dll",), size=900 * 1024)
    game("2000", "Manual Game", "ManualGame", "Manual.exe", ("d3d11.dll",))
    game("3000", "Retro Racer", "RetroRacer", "racer.exe", ("d3d9.dll",), arch="32", sdcard=True)
    game("4000", "Vulkan Quest", "VulkanQuest", "vq.exe", ("vulkan-1.dll",))
    arena = game("5000", "Arena Online", "Arena", "Arena.exe", ("d3d11.dll",))
    (arena / "EasyAntiCheat").mkdir(exist_ok=True)
    (arena / "EasyAntiCheat" / "EasyAntiCheat_EOS_Setup.exe").write_bytes(b"x")
    native = fake.add_game("6000", "Native Penguin", "Penguin")
    (native / "penguin.x86_64").write_bytes(b"\x7fELF")
    game("7000", "Bright Lights", "BrightLights", "Bright.exe", ("d3d12.dll",))
    game("8000", "Old Classic", "OldClassic", "classic.exe", ("d3d11.dll",))
    indie = fake.root / "home" / "deck" / "Games" / "IndieDarling"
    exe = make_pe(indie / "IndieDarling.exe", imports=("d3d11.dll",), size=300 * 1024)
    fake.add_shortcut("Indie Darling (non-Steam)", str(exe), str(indie))
    (fake.plugin_dir / "compatibility.json").write_text(json.dumps(COMPAT), encoding="utf-8")
    downloads = fake.home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "renodx-manualgame.addon64").write_bytes(b"addon" * 400)


def fake_download(url, target, **_kwargs):
    time.sleep(1.0)  # let the UI show its busy state
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"addon" * 400)
    return target


def make_plugin(args: argparse.Namespace):
    if args.reset and SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    fresh = not SANDBOX.exists()
    fake = FakeSteam(SANDBOX)
    if fresh:
        build_library(fake)
    sys.modules["decky"] = types.SimpleNamespace(
        logger=logging.getLogger("decky"), DECKY_USER="deck",
        DECKY_USER_HOME=str(fake.home), DECKY_PLUGIN_DIR=str(fake.plugin_dir),
    )
    import main  # noqa: E402

    main.decky = sys.modules["decky"]
    plugin = main.Plugin()
    service = plugin.service
    service.runtime = OfflineRuntime(fake.paths)
    service.renodx._fetch_text = lambda _url: WIKI
    service.rhi._fetch_text = lambda _url: json.dumps(RHI)
    service.compat.refresh_due = lambda now=None: False  # keep the fake compatibility.json; stay offline
    service.pcgw.game_data = lambda appid: dict(PCGW.get(str(appid), {"native_hdr": "unknown", "graphics_api": "unknown"}))
    hdr = {"on": (True, True), "off": (True, False), "unknown": (None, None)}[args.hdr]
    service.display_status = lambda: {"status": "success", "supported": hdr[0], "enabled": hdr[1], "game_mode": hdr[0] is not None}
    plugin.updater.check = lambda force=False: {"ok": True, "current": plugin.version, "latest": plugin.version, "elevated": True, "hasUpdate": False, "canInstall": False, "message": "You have the latest version."}
    mock.patch("backend.service.net.download", side_effect=fake_download).start()
    return plugin


def serve(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    holder = {}

    def start(reset: bool) -> None:
        args.reset = reset
        holder["plugin"] = make_plugin(args)
        asyncio.run_coroutine_threadsafe(holder["plugin"]._main(), loop).result()

    start(args.reset)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DEV), **kw)

        def log_message(self, fmt, *a):  # quieter
            if a and "/rpc/" in str(a[0]):
                logging.info(fmt % a)

        def do_POST(self):
            if self.path == "/dev/reset":  # fresh fake library, used by dev/screens.mjs
                start(True)
                self.send_response(204)
                self.end_headers()
                return
            if not self.path.startswith("/rpc/"):
                self.send_error(404)
                return
            name = self.path[len("/rpc/"):]
            method = getattr(holder["plugin"], name, None)
            if name.startswith("_") or not callable(method):
                self.send_error(404, f"No RPC {name}")
                return
            length = int(self.headers.get("Content-Length") or 0)
            call_args = json.loads(self.rfile.read(length) or b"[]")
            result = asyncio.run_coroutine_threadsafe(method(*call_args), loop).result()
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Decky RenoDX dev harness: http://127.0.0.1:{args.port}/  (sandbox: {SANDBOX})")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--hdr", choices=["on", "off", "unknown"], default="on", help="what the fake display reports")
    parser.add_argument("--reset", action="store_true", help="recreate the fake Steam library")
    serve(parser.parse_args())
