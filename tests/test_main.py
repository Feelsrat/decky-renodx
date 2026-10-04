import asyncio
import importlib
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures import FakeSteam, make_pe  # noqa: E402


class MainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeSteam()
        self.addCleanup(self.fake.cleanup)
        decky = types.SimpleNamespace(
            logger=logging.getLogger("decky-test"),
            DECKY_USER="deck",
            DECKY_USER_HOME=str(self.fake.home),
            DECKY_PLUGIN_DIR=str(self.fake.plugin_dir),
        )
        sys.modules["decky"] = decky
        self.main = importlib.reload(importlib.import_module("main"))
        self.plugin = self.main.Plugin()

    async def test_rpc_surface(self):
        root = self.fake.add_game("10", "Game", "Game")
        make_pe(root / "Game.exe", imports=("d3d11.dll",))
        with mock.patch.object(self.plugin.service.pcgw, "game_data", return_value={}), \
                mock.patch.object(self.plugin.service.rhi, "game", return_value={}), \
                mock.patch.object(self.plugin.service.renodx, "mods", return_value=[]):
            games = await self.plugin.list_installed_games()
            self.assertEqual(games["games"], [{"appid": "10", "name": "Game", "kind": "steam", "renodx": False}])
            state = await self.plugin.get_game_state("10")
            badge = await self.plugin.get_library_badge("10")
        self.assertEqual(state["status"], "success", state)
        self.assertEqual(state["context"]["api"], "d3d11")
        self.assertEqual(badge, {"status": "success", "level": "none"})
        missing = await self.plugin.get_game_state("999")
        self.assertEqual(missing["status"], "error")

    async def test_second_change_for_same_game_is_refused(self):
        started = asyncio.Event()
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_uninstall(_appid):
            loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result()
            return {"status": "success"}

        with mock.patch.object(self.plugin.service, "uninstall", side_effect=slow_uninstall):
            first = asyncio.create_task(self.plugin.remove_hdr("10"))
            await started.wait()
            second = await self.plugin.remove_hdr("10")
            other_game = asyncio.create_task(self.plugin.remove_hdr("11"))
            release.set()
            self.assertTrue(second["busy"])
            self.assertEqual((await first)["status"], "success")
            self.assertEqual((await other_game)["status"], "success")

    async def test_unexpected_errors_become_messages(self):
        with mock.patch.object(self.plugin.service, "list_games", side_effect=KeyError("boom")):
            result = await self.plugin.list_installed_games()
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])


if __name__ == "__main__":
    unittest.main()
