import io
import json
import logging
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import compat, fsutil, launch, pcgw, recommend, renodx, state, transaction, updater, vdf  # noqa: E402
from backend.pe import read_pe  # noqa: E402
from backend.service import HdrService  # noqa: E402
from backend.steam import SteamLibrary  # noqa: E402
from tests.fixtures import FakeSteam, OfflineRuntime, make_pe, tree_digest  # noqa: E402

LOG = logging.getLogger("tests")

WIKI = """
# List
| Name | Maintainer | Links | Status |
| --- | --- | --- | --- |
| Bayonetta | ShortFuse | [![Snapshot](badge)](https://example.com/renodx-bayonetta.addon32) | :white_check_mark: |
| DOOM | Dev | [![Snapshot](badge)](https://example.com/renodx-doom.addon64) | :white_check_mark: |
| Final Fantasy X | Dev | [![Snapshot](badge)](https://example.com/renodx-ffx.addon64) | :white_check_mark: |
| Code Vein | Dev | [![Snapshot](badge)](https://example.com/renodx-codevein.addon64) | :white_check_mark: |
| Manual Game | Dev | [![Nexus Mods](badge)](https://www.nexusmods.com/game/mods/1) | :construction: |
| Shippy | Dev | [![Snapshot](badge)](https://example.com/renodx-shippy.addon64) | :white_check_mark: |

## Multi-Game Mods
### Unreal Engine [![Snapshot](badge)](https://example.com/renodx-unrealengine.addon64)
| Name | Status | Notes |
| --- | --- | --- |
| Sand Land | :white_check_mark: | Use output size upgrade. |
"""


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSteam()
        self.service = HdrService(self.fake.paths, "1.0.0")
        self.service.runtime = OfflineRuntime(self.fake.paths)
        self.service.renodx = renodx.RenoDXCatalog(self.fake.paths.cache / "mods.json", lambda _url: WIKI)
        self.service.pcgw.game_data = lambda appid: {"native_hdr": "unknown", "graphics_api": "unknown"}
        self.downloads: list[str] = []

        def fake_download(url, target, **_kwargs):
            self.downloads.append(url)
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"addon" * 400)
            return target

        patcher = mock.patch("backend.service.net.download", side_effect=fake_download)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.fake.cleanup)

    def unreal_game(self, appid="1000", name="Shippy", sdcard=False):
        root = self.fake.add_game(appid, name, name, sdcard=sdcard)
        make_pe(root / f"{name}.exe", size=200 * 1024)  # launcher stub
        shipping = make_pe(root / name / "Binaries" / "Win64" / f"{name}-Win64-Shipping.exe", imports=("d3d12.dll", "kernel32.dll"), size=900 * 1024)
        (root / name / "Content" / "Shaders").mkdir(parents=True)
        (root / name / "Content" / "Shaders" / "game.bin").write_bytes(b"game data")
        (root / name / "Binaries" / "Win64" / "Shaders").mkdir(parents=True)
        (root / name / "Binaries" / "Win64" / "Shaders" / "keep.bin").write_bytes(b"do not delete")
        return root, shipping


class SteamTests(ServiceCase):
    def test_libraries_and_non_ascii_names(self):
        self.fake.add_game("10", "Pokémon™ Legends", "Poke")
        self.fake.add_game("20", "SD Game", "SDGame", sdcard=True)
        self.fake.add_game("228980", "Steamworks Common Redistributables", "Redist")
        names = {game["name"] for game in self.service.list_games()["games"]}
        self.assertEqual(names, {"Pokémon™ Legends", "SD Game"})

    def test_compatdata_follows_the_games_library(self):
        self.fake.add_game("20", "SD Game", "SDGame", sdcard=True)
        app = SteamLibrary(self.fake.home).app("20")
        self.assertEqual(app.compatdata, self.fake.sdcard / "steamapps" / "compatdata" / "20")

    def test_console_log_appid_is_matched_on_word_boundary(self):
        root = self.fake.add_game("70", "Game", "Game")
        exe = make_pe(root / "Game.exe")
        logs = self.fake.steam / "logs"
        logs.mkdir()
        (logs / "console-linux.txt").write_text(f"AppId=700 -- '{root}/Other.exe'\nAppId=70 -- '{exe}'\n", encoding="utf-8")
        self.assertEqual(SteamLibrary(self.fake.home).launched_executable("70", root), exe)
        self.assertIsNone(SteamLibrary(self.fake.home).launched_executable("7", root))

    def test_vdf_escapes(self):
        data = vdf.loads('"a" { "path" "C:\\\\Games\\\\X" "q" "say \\"hi\\"" }')
        self.assertEqual(data["a"]["path"], "C:\\Games\\X")
        self.assertEqual(data["a"]["q"], 'say "hi"')


class ShortcutTests(ServiceCase):
    def test_non_steam_game_is_listed_and_installable(self):
        folder = self.fake.root / "Games" / "Indie"
        exe = make_pe(folder / "bin" / "Indie.exe", imports=("d3d11.dll",))
        self.fake.add_shortcut("Indie Game", str(exe), str(folder), launch_options="-windowed")
        games = self.service.list_games()["games"]
        self.assertEqual(games, [{"appid": str(0x9ABCDEF0), "name": "Indie Game", "kind": "shortcut"}])
        appid = games[0]["appid"]
        state_ = self.service.game_state(appid)
        self.assertEqual((state_["kind"], state_["exe_path"], state_["launch_options_hint"]), ("shortcut", str(exe), "-windowed"))
        app = self.service.steam.app(appid)
        self.assertEqual(app.compatdata, self.fake.steam / "steamapps" / "compatdata" / appid)
        result = self.service.install(appid, "reshade")
        self.assertEqual(result["status"], "success", result)
        self.assertTrue((exe.parent / "dxgi.dll").exists())

    def test_launcher_shortcuts_are_skipped(self):
        self.fake.add_shortcut("Lutris Game", "/usr/bin/flatpak", "/home/deck")
        self.assertEqual(self.service.list_games()["games"], [])

    def test_shortcut_without_stored_appid_uses_steams_crc(self):
        import binascii
        exe = make_pe(self.fake.root / "Old" / "Old.exe")
        self.fake.add_shortcut("Old", str(exe), str(exe.parent), appid=None)
        expected = str((binascii.crc32(f'"{exe}"Old'.encode()) | 0x80000000) & 0xFFFFFFFF)
        self.assertEqual(self.service.list_games()["games"][0]["appid"], expected)


class DetectionTests(ServiceCase):
    def test_pe_reader(self):
        info = read_pe(make_pe(self.fake.root / "x.exe", arch="32", imports=("d3d9.dll",)))
        self.assertEqual((info.arch, info.imports), ("32", {"d3d9.dll"}))

    def test_unreal_targets_shipping_exe(self):
        root, shipping = self.unreal_game()
        state_ = self.service.game_state("1000")
        self.assertEqual(state_["exe_path"], str(shipping))
        ctx = state_["context"]
        self.assertEqual((ctx["api"], ctx["hook"], ctx["engine"], ctx["architecture"]), ("d3d12", "dxgi", "unreal", "64"))

    def test_anti_cheat_found_at_install_root(self):
        root, _shipping = self.unreal_game()
        (root / "EasyAntiCheat").mkdir()
        (root / "EasyAntiCheat" / "EasyAntiCheat_EOS_Setup.exe").write_bytes(b"x")
        state_ = self.service.game_state("1000")
        self.assertEqual(state_["context"]["anti_cheat"], ["EasyAntiCheat"])
        available = {option["method"]: option["available"] for option in state_["method_options"]}
        self.assertFalse(available["renodx"] or available["reshade"] or available["special_k"])
        self.assertEqual(state_["recommendations"][0]["method"], "sdr")

    def test_skip_tokens_do_not_drop_real_games(self):
        root = self.fake.add_game("5", "Peace Walker", "PW")
        make_pe(root / "PeaceWalker.exe", imports=("d3d11.dll",))
        make_pe(root / "unins000.exe")
        self.assertTrue(self.service.game_state("5")["exe_path"].endswith("PeaceWalker.exe"))

    def test_linux_build_is_reported(self):
        root = self.fake.add_game("6", "Native", "Native")
        (root / "game.x86_64").write_bytes(b"\x7fELF")
        state_ = self.service.game_state("6")
        self.assertTrue(state_["context"]["linux_build"])
        self.assertIn("Linux", next(o for o in state_["method_options"] if o["method"] == "reshade")["reason"])


class RenoDXTests(unittest.TestCase):
    def setUp(self):
        self.mods = renodx.parse_mods(WIKI)

    def best(self, title, **kwargs):
        catalog = renodx.RenoDXCatalog(Path("/nonexistent/cache.json"), lambda _url: WIKI)
        catalog._mods, catalog._fetched_at = self.mods, 1e18
        return catalog.match(title, **kwargs)

    def test_parser(self):
        bayonetta = next(mod for mod in self.mods if mod["name"] == "Bayonetta")
        self.assertEqual((bayonetta["status"], bayonetta["bitness"], bayonetta["source_type"]), ("working", "32", "snapshot"))
        manual = next(mod for mod in self.mods if mod["name"] == "Manual Game")
        self.assertEqual((manual["addon_url"], manual["manual_url"]), ("", "https://www.nexusmods.com/game/mods/1"))

    def test_sequels_and_different_games_do_not_match(self):
        self.assertIsNone(self.best("DOOM Eternal"))
        self.assertIsNone(self.best("Final Fantasy XIII"))

    def test_editions_and_punctuation_match(self):
        self.assertEqual(self.best("Code Vein GOTY Edition")["name"], "Code Vein")
        self.assertEqual(self.best("SAND LAND")["match_type"], "generic_listed")

    def test_generic_engine_needs_64_bit(self):
        self.assertEqual(self.best("Some Unreal Game", engine="unreal", architecture="64")["match_type"], "generic_engine")
        self.assertIsNone(self.best("Some Unreal Game", engine="unreal", architecture="32"))

    def test_compat_alias(self):
        self.assertEqual(self.best("BAYONETTA™ (2009)", aliases=["Bayonetta"])["name"], "Bayonetta")

    def test_stale_cache_used_when_offline(self):
        with mock.patch.object(fsutil, "write_json"):
            catalog = renodx.RenoDXCatalog(Path("/nonexistent"), lambda _url: (_ for _ in ()).throw(OSError("offline")))
            catalog._mods, catalog._fetched_at = self.mods, 0
            self.assertTrue(catalog.mods())


class RecommendTests(unittest.TestCase):
    base = {"exe_found": True, "architecture": "64", "api": "d3d11", "engine": "unknown"}

    def test_32_bit_is_not_hard_blocked(self):
        recs, options = recommend.evaluate({**self.base, "architecture": "32", "api": "d3d9"})
        self.assertTrue(next(o for o in options if o["method"] == "reshade")["available"])
        self.assertIn("reshade", [rec["method"] for rec in recs])

    def test_vulkan_blocks_proxy_methods(self):
        _recs, options = recommend.evaluate({**self.base, "api": "vulkan"})
        available = {o["method"]: o["available"] for o in options}
        self.assertFalse(available["reshade"] or available["special_k"])

    def test_specific_renodx_beats_native_hdr_and_plan_falls_back(self):
        match = {"name": "X", "match_type": "specific", "addon_url": "https://x/a.addon64", "bitness": "64"}
        recs, _ = recommend.evaluate({**self.base, "native_hdr": "true", "renodx_match": match})
        self.assertEqual(recs[0]["method"], "renodx")
        self.assertEqual(recommend.install_plan(recs), ["renodx"])
        recs, _ = recommend.evaluate({**self.base, "renodx_match": match})
        self.assertEqual(recommend.install_plan(recs), ["renodx", "special_k", "reshade"])

    def test_native_hdr_stops_the_plan(self):
        recs, _ = recommend.evaluate({**self.base, "native_hdr": "true"})
        self.assertEqual(recommend.install_plan(recs), [])

    def test_bitness_mismatch_blocks_renodx(self):
        match = {"name": "X", "match_type": "specific", "addon_url": "https://x/a.addon32", "bitness": "32"}
        _recs, options = recommend.evaluate({**self.base, "renodx_match": match})
        self.assertFalse(next(o for o in options if o["method"] == "renodx")["available"])


class TransactionTests(unittest.TestCase):
    def test_install_then_revert_is_byte_identical(self):
        fake = FakeSteam()
        self.addCleanup(fake.cleanup)
        game = fake.root / "game"
        game.mkdir()
        (game / "dxgi.dll").write_bytes(b"game's own dxgi")
        (game / "Shaders").mkdir()
        (game / "Shaders" / "a.bin").write_bytes(b"a")
        before = tree_digest(game)
        tx = transaction.Transaction(LOG)
        tx.copy_file(fake.root / "game" / "Shaders" / "a.bin", game / "dxgi.dll")
        tx.write_text(game / "new" / "deep" / "x.ini", "x")
        source = fake.root / "src"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "f").write_text("f")
        tx.copy_tree(source, game / "Shaders")
        tx.track_artifacts(game, ["ReShade.log"])
        (game / "ReShade.log").write_text("runtime log")
        self.assertNotEqual(tree_digest(game), before)
        self.assertEqual(transaction.revert(tx.to_record(), LOG)[0], [])
        self.assertEqual(tree_digest(game), before)

    def test_stash_put_back_and_commit(self):
        fake = FakeSteam()
        self.addCleanup(fake.cleanup)
        game = fake.root / "game"
        game.mkdir()
        (game / "d3d9.dll").write_bytes(b"original")
        original = tree_digest(game)
        tx = transaction.Transaction(LOG)
        tx.write_text(game / "d3d9.dll", "ours")
        tx.copy_tree(fake.plugin_dir, game / "ReShade_shaders")
        installed = tree_digest(game)
        stash = transaction.Stash(tx.to_record(), LOG)
        stash.take_apart()
        visible = {key: value for key, value in tree_digest(game).items() if not key.startswith(".decky-renodx")}
        self.assertEqual(visible, original)
        stash.put_back()
        self.assertEqual(tree_digest(game), installed)
        stash = transaction.Stash(tx.to_record(), LOG)
        stash.take_apart()
        stash.commit()
        self.assertEqual(tree_digest(game), original)


class SteamReinstallTests(unittest.TestCase):
    """Steam can wipe a game folder (and our backups) while the install record survives."""

    def setUp(self):
        self.fake = FakeSteam()
        self.addCleanup(self.fake.cleanup)
        self.game = self.fake.root / "game"
        self.game.mkdir()
        (self.game / "dxgi.dll").write_bytes(b"game original")
        self.tx = transaction.Transaction(LOG)
        self.tx.write_text(self.game / "dxgi.dll", "ours")
        # Steam "verify files": our dll replaced by a fresh original, backups gone.
        (self.game / ".decky-renodx" / "backup" / "dxgi.dll").unlink()
        (self.game / "dxgi.dll").write_bytes(b"fresh from steam")

    def test_uninstall_leaves_unprovable_files(self):
        errors, remaining = transaction.revert(self.tx.to_record(), LOG)
        self.assertEqual((errors, remaining["replaced"]), ([], {}))
        self.assertEqual((self.game / "dxgi.dll").read_bytes(), b"fresh from steam")

    def test_switch_commit_never_deletes_it(self):
        stash = transaction.Stash(self.tx.to_record(), LOG)
        stash.take_apart()
        stash.commit()
        self.assertEqual((self.game / "dxgi.dll").read_bytes(), b"fresh from steam")

    def test_retry_after_partial_failure_keeps_restored_originals(self):
        tx = transaction.Transaction(LOG)
        (self.game / "d3d9.dll").write_bytes(b"d3d9 original")
        tx.write_text(self.game / "d3d9.dll", "ours")
        record = tx.to_record()
        errors, remaining = transaction.revert(record, LOG)
        self.assertEqual(errors, [])
        transaction.revert(remaining, LOG)
        self.assertEqual((self.game / "d3d9.dll").read_bytes(), b"d3d9 original")


class InstallFlowTests(ServiceCase):
    def test_failed_switch_keeps_empty_folders_of_previous_install(self):
        source = self.fake.root / "tree"
        (source / "sub" / "empty").mkdir(parents=True)
        game = self.fake.root / "g"
        game.mkdir()
        tx = transaction.Transaction(LOG)
        tx.copy_tree(source, game / "pack")
        stash = transaction.Stash(tx.to_record(), LOG)
        stash.take_apart()
        failing = transaction.Transaction(LOG)
        failing.write_text(game / "x.ini", "x")
        failing.rollback()
        stash.put_back()
        self.assertTrue((game / "pack" / "sub" / "empty").is_dir())

    def test_import_with_unnamed_archive_keeps_other_imports(self):
        self.unreal_game()
        keep = self.fake.paths.imports / "downloads" / "other.addon64"
        keep.parent.mkdir(parents=True)
        keep.write_bytes(b"keep")
        downloads = self.fake.home / "Downloads"
        downloads.mkdir()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as handle:
            handle.writestr("renodx-shippy.addon64", b"addon")
        (downloads / "(1).zip").write_bytes(buffer.getvalue())
        self.assertEqual(self.service.import_renodx("1000", str(downloads / "(1).zip"))["status"], "success")
        self.assertTrue(keep.exists())

    def test_reset_prefix_returns_delayed_launch_to_strip(self):
        root = self.fake.add_game("3000", "Delayed", "Delayed")
        make_pe(root / "Delayed.exe", imports=("d3d11.dll",))
        self.fake.paths.plugin_dir.joinpath("compatibility.json").write_text(json.dumps({"games": {"3000": {"name": "Delayed", "tools": {"special_k": {
            "automation": {"preferred_injection": "global_delayed"}}}}}}), encoding="utf-8")
        self.service.compat.reload()
        self.fake.compatdata("3000")
        installed = self.service.install("3000", "special_k_delayed")
        result = self.service.reset_prefix("3000")
        self.assertEqual(result["launch"], installed["launch"])

    def test_reshade_install_and_remove_restores_game(self):
        root, shipping = self.unreal_game()
        exe_dir = shipping.parent
        (exe_dir / "dxgi.dll").write_bytes(b"game shipped dxgi")
        before = tree_digest(root)
        result = self.service.install("1000", "reshade")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual((exe_dir / "dxgi.dll").read_bytes()[:2], b"MZ")
        self.assertTrue((exe_dir / "ReShade_shaders" / "Merged" / "Shaders" / "AutoHDR.fx").exists())
        self.assertTrue((exe_dir / "AutoHDR.addon64").exists())
        self.assertEqual(result["launch"]["dll_overrides"], {"dxgi": "n,b"})
        self.assertNotIn("PROTON_LOG", result["launch"]["env"])
        (exe_dir / "ReShade.log").write_text("ReShade loaded")
        status = self.service.install_status("1000")
        self.assertTrue(status["installed"] and status["files_ok"])
        removed = self.service.uninstall("1000")
        self.assertEqual(removed["status"], "success", removed)
        self.assertEqual(tree_digest(root), before)
        self.assertFalse(self.service.install_status("1000")["installed"])

    def test_recommended_installs_renodx_from_wiki(self):
        _root, shipping = self.unreal_game()
        result = self.service.install("1000", "recommended")
        self.assertEqual(result["method"], "renodx", result)
        self.assertTrue((shipping.parent / "renodx-shippy.addon64").exists())
        self.assertTrue((shipping.parent / "zzz_display_commander.addon64").exists())
        ini = (shipping.parent / "ReShade.ini").read_text()
        self.assertIn("EffectSearchPaths=\n", ini)
        self.assertFalse((shipping.parent / "ReShade_shaders").exists())

    def test_failed_switch_keeps_previous_install(self):
        root, shipping = self.unreal_game()
        self.assertEqual(self.service.install("1000", "reshade")["status"], "success")
        installed = tree_digest(root)
        record = self.service.store.get("1000")
        self.service.runtime.fail = {"specialk"}
        result = self.service.install("1000", "special_k")
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["kept_previous"])
        self.assertEqual(tree_digest(root), installed)
        self.assertEqual(self.service.store.get("1000")["installed_at"], record["installed_at"])

    def test_switch_methods_then_remove(self):
        root, shipping = self.unreal_game()
        before = tree_digest(root)
        self.assertEqual(self.service.install("1000", "reshade")["status"], "success")
        result = self.service.install("1000", "special_k")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["previous_launch"]["dll_overrides"], {"dxgi": "n,b"})
        self.assertFalse((shipping.parent / "ReShade.ini").exists())
        self.assertTrue((shipping.parent / "dxgi.ini").exists())
        self.service.uninstall("1000")
        self.assertEqual(tree_digest(root), before)

    def test_manual_renodx_returns_download_info(self):
        root = self.fake.add_game("2000", "Manual Game", "Manual")
        make_pe(root / "Manual.exe", imports=("d3d11.dll",))
        result = self.service.install("2000", "renodx")
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual(result["url"], "https://www.nexusmods.com/game/mods/1")
        self.assertIsNone(self.service.store.get("2000"))

    def test_import_zip_installs_addon(self):
        _root, shipping = self.unreal_game()
        downloads = self.fake.home / "Downloads"
        downloads.mkdir()
        archive = downloads / "renodx-shippy.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as handle:
            handle.writestr("mod/renodx-shippy.addon64", b"addon")
        archive.write_bytes(buffer.getvalue())
        self.assertEqual(self.service.recent_downloads()["files"][0]["path"], str(archive))
        result = self.service.import_renodx("1000", str(archive))
        self.assertEqual(result["status"], "success", result)
        self.assertTrue((shipping.parent / "renodx-shippy.addon64").exists())

    def test_import_outside_home_is_refused(self):
        self.unreal_game()
        self.assertEqual(self.service.import_renodx("1000", "/etc/passwd")["status"], "error")

    def test_specialk_delayed_needs_prefix_and_uses_wrapper(self):
        root = self.fake.add_game("3000", "Delayed", "Delayed", sdcard=True)
        make_pe(root / "Delayed.exe", imports=("d3d11.dll",))
        self.fake.paths.plugin_dir.joinpath("compatibility.json").write_text(json.dumps({"games": {"3000": {"name": "Delayed", "tools": {"special_k": {
            "special_k_delay_seconds": 7, "automation": {"preferred_injection": "global_delayed"}}}}}}), encoding="utf-8")
        self.service.compat.reload()
        result = self.service.install("3000", "special_k_delayed")
        self.assertEqual(result["status"], "error")
        self.assertIn("Launch the game once", result["message"])
        prefix = self.fake.compatdata("3000", sdcard=True)
        result = self.service.install("3000", "special_k_delayed")
        self.assertEqual(result["status"], "success", result)
        wrapper = result["launch"]["wrapper"]
        self.assertEqual(wrapper[2:4], ["3000", "7"])
        self.assertTrue(Path(wrapper[1]).is_file())
        self.assertTrue(str(prefix) in wrapper[4])
        changed = self.service.set_specialk_delay("3000", 12)
        self.assertEqual(changed["launch"]["wrapper"][3], "12")
        self.assertEqual(self.service.uninstall("3000")["status"], "success")
        self.assertFalse((prefix / "pfx" / "drive_c" / "users").exists())

    def test_reset_prefix_refuses_running_game(self):
        self.fake.add_game("4000", "Running", "Running")
        self.fake.compatdata("4000")
        with mock.patch("backend.service.running_appids", return_value={"4000"}):
            self.assertEqual(self.service.reset_prefix("4000")["status"], "error")
        self.assertEqual(self.service.reset_prefix("4000")["status"], "success")


class RepairTests(ServiceCase):
    def test_repair_after_steam_verify_reinstalls_renodx_from_kept_copy(self):
        _root, shipping = self.unreal_game()
        self.assertEqual(self.service.install("1000", "renodx")["status"], "success")
        addon = shipping.parent / "renodx-shippy.addon64"
        addon.unlink()  # "verify integrity" removed it
        status = self.service.install_status("1000")
        self.assertTrue(status["needs_repair"])
        self.downloads.clear()
        result = self.service.repair("1000")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.downloads, [], "repair should not need the network")
        self.assertTrue(addon.exists())
        self.assertFalse(self.service.install_status("1000")["needs_repair"])

    def test_game_update_and_old_launch_options_are_flagged(self):
        root, _shipping = self.unreal_game()
        self.service.install("1000", "reshade")
        record = self.service.store.get("1000")
        record["buildid"] = "1"
        record["launch"]["env"]["ENABLE_HDR_WSI"] = "1"
        self.service.store.put("1000", record)
        manifest = self.fake.steam / "steamapps" / "appmanifest_1000.acf"
        manifest.write_text(manifest.read_text().replace("}", '\t"buildid"\t\t"2"\n}'))
        status = self.service.install_status("1000")
        self.assertTrue(status["game_updated"] and status["launch_outdated"] and status["needs_repair"])

    def test_result_is_tied_to_the_installed_method(self):
        self.unreal_game()
        self.service.install("1000", "special_k")
        self.service.set_result("1000", "worked")
        self.assertEqual(self.service.game_state("1000")["user_result"], "worked")
        self.assertTrue(self.service.settings.game("1000")["specialk_verified"])
        self.service.install("1000", "reshade")
        self.assertEqual(self.service.game_state("1000")["user_result"], "")

    def test_launch_keep_is_stored(self):
        self.unreal_game()
        self.service.install("1000", "reshade")
        self.service.set_launch_keep("1000", {"args": ["-dx11"], "dlls": ["DXGI"], "env": []})
        self.assertEqual(self.service.store.get("1000")["launch"]["keep"], {"args": ["-dx11"], "dlls": ["dxgi"], "env": []})


class RepairEdgeTests(ServiceCase):
    def test_repair_migrates_legacy_renodx_reusing_its_addon(self):
        _root, shipping = self.unreal_game()
        exe_dir = shipping.parent
        (exe_dir / ".decky-renodx-hdr.json").write_text(json.dumps({"method": "renodx", "dll": "dxgi"}))
        make_pe(exe_dir / "dxgi.dll", marker=b"ReShade", size=4096)
        (exe_dir / "renodx-oldmod.addon64").write_bytes(b"old imported mod")
        self.downloads.clear()
        result = self.service.repair("1000")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.downloads, [])
        self.assertEqual((exe_dir / "renodx-oldmod.addon64").read_bytes(), b"old imported mod")
        self.assertFalse(self.service.install_status("1000")["legacy"])

    def test_repair_of_010_renodx_uses_installed_addon(self):
        _root, shipping = self.unreal_game()
        self.service.install("1000", "renodx")
        record = self.service.store.get("1000")
        record["extra"].pop("source_addon")
        record["launch"]["env"]["ENABLE_HDR_WSI"] = "1"
        self.service.store.put("1000", record)
        self.downloads.clear()
        result = self.service.repair("1000")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.downloads, [])
        self.assertFalse(self.service.install_status("1000")["needs_repair"])

    def test_repair_keeps_special_k_delay(self):
        root = self.fake.add_game("3000", "Delayed", "Delayed")
        make_pe(root / "Delayed.exe", imports=("d3d11.dll",))
        self.fake.paths.plugin_dir.joinpath("compatibility.json").write_text(json.dumps({"games": {"3000": {"name": "Delayed", "tools": {"special_k": {
            "automation": {"preferred_injection": "global_delayed"}}}}}}), encoding="utf-8")
        self.service.compat.reload()
        self.fake.compatdata("3000")
        self.service.install("3000", "special_k_delayed")
        self.service.set_specialk_delay("3000", 20)
        result = self.service.repair("3000")
        self.assertEqual(result["launch"]["wrapper"][3], "20")

    def test_native_hdr_is_recorded_so_it_can_be_replaced_and_removed(self):
        self.unreal_game()
        native = self.service.install("1000", "native_hdr")
        self.assertTrue(self.service.install_status("1000")["installed"])
        switched = self.service.install("1000", "reshade")
        self.assertEqual(switched["previous_launch"]["env"], native["launch"]["env"])
        self.service.install("1000", "native_hdr")
        removed = self.service.uninstall("1000")
        self.assertEqual(removed["launch"]["env"], native["launch"]["env"])
        self.assertFalse(self.service.install_status("1000")["installed"])


class LegacyTests(ServiceCase):
    def test_legacy_cleanup_keeps_game_data(self):
        root, shipping = self.unreal_game()
        exe_dir = shipping.parent
        (exe_dir / ".decky-renodx-hdr.json").write_text(json.dumps({"method": "reshade-hdr", "dll": "dxgi"}))
        make_pe(exe_dir / "dxgi.dll", marker=b"ReShade", size=4096)
        make_pe(exe_dir / "d3d11.dll", size=4096)  # the game's own
        (exe_dir / "ReShade.ini").write_text("x")
        (exe_dir / "ReShade_shaders").mkdir()
        (exe_dir / "d3dcompiler_47.dll").write_bytes(b"compiler")
        status = self.service.install_status("1000")
        self.assertTrue(status["legacy"])
        self.assertEqual(self.service.uninstall("1000")["status"], "success")
        self.assertFalse((exe_dir / "dxgi.dll").exists())
        self.assertFalse((exe_dir / "ReShade_shaders").exists())
        self.assertTrue((exe_dir / "d3d11.dll").exists())
        self.assertTrue((exe_dir / "Shaders" / "keep.bin").exists())
        self.assertTrue((exe_dir / "d3dcompiler_47.dll").exists())


class MiscTests(unittest.TestCase):
    def test_launch_preview_quotes_paths(self):
        spec = launch.spec("", wrapper=["bash", "/home/deck/My Mods/w.sh", "1"], args=["-dx11"])
        self.assertTrue(spec["preview"].startswith("PROTON_ENABLE_HDR=1"))
        self.assertIn('"/home/deck/My Mods/w.sh"', spec["preview"])
        self.assertTrue(spec["preview"].endswith("%command% -dx11"))

    def test_hdr_env_is_gamescope_safe(self):
        env = launch.spec("dxgi")["env"]
        self.assertEqual(env, {"PROTON_ENABLE_HDR": "1", "DXVK_HDR": "1"})

    def test_display_status_parsing(self):
        from backend import display
        out = "GAMESCOPE_DISPLAY_SUPPORTS_HDR(CARDINAL) = 1\nGAMESCOPE_DISPLAY_HDR_ENABLED(CARDINAL) = 0\n"
        self.assertEqual(display.parse_xprop(out), {"supported": True, "enabled": False})
        self.assertEqual(display.parse_xprop("GAMESCOPE_DISPLAY_HDR_ENABLED:  not found."), {"supported": None, "enabled": None})

    def test_renodx_settings_become_manual_steps(self):
        fake = FakeSteam()
        self.addCleanup(fake.cleanup)
        (fake.plugin_dir / "compatibility.json").write_text(json.dumps({"games": {"5": {"name": "X", "tools": {"renodx": {
            "automation": {"renodx_settings": {"upgrades": [{"format": "R10G10B10A2_UNORM", "mode": "Output Size"}]}}}}}}}))
        db = compat.CompatDB(fake.plugin_dir / "compatibility.json", fake.root / "none.json")
        self.assertEqual(db.metadata("5", "renodx")["manual_steps"], ['In the RenoDX tab, set the R10G10B10A2_UNORM upgrade to "Output Size".'])

    def test_compat_merge_is_per_game(self):
        merged = compat.merge({"games": {"1": {"name": "a"}, "2": {"name": "b"}}}, {"games": {"2": {"name": "B"}}})
        self.assertEqual(merged["games"], {"1": {"name": "a"}, "2": {"name": "B"}})

    def test_pcgw_helpers(self):
        self.assertEqual(pcgw.api_from_fields("9, 11", "", ""), "d3d11")
        self.assertEqual(pcgw.api_from_fields("", "", "1.2"), "vulkan")
        text = "== Essential improvements ==\n* Fix one\n=== Skip intro ===\n== Other ==\n* no"
        self.assertEqual(pcgw.extract_section(text, "Essential improvements"), ["Fix one", "Skip intro"])

    def test_version_parsing(self):
        self.assertGreater(updater.parse_version("0.1.0"), updater.parse_version("0.0.81"))
        self.assertEqual(updater.parse_version("v1.2.3-beta.1"), (1, 2, 3))

    def test_update_requires_digest(self):
        fake = FakeSteam()
        self.addCleanup(fake.cleanup)
        release = [{"tag_name": "v9.0.0", "assets": [{"name": "decky-renodx.zip", "browser_download_url": "https://x/z.zip"}]}]
        up = updater.Updater(fake.plugin_dir, "0.1.0", fetch_json=lambda *a, **k: release, download=mock.Mock())
        with mock.patch.object(updater.Updater, "elevated", return_value=True):
            result = up.install()
        self.assertFalse(result["ok"])
        self.assertIn("digest", result["message"])

    def test_safe_zip_extract_rejects_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as handle:
            handle.writestr("../evil", b"x")
        fake = FakeSteam()
        self.addCleanup(fake.cleanup)
        target = fake.root / "extract"
        with zipfile.ZipFile(buffer) as handle, self.assertRaises(ValueError):
            fsutil.safe_extract_zip(handle, target)
        self.assertFalse(target.exists())
        self.assertFalse((fake.root / "evil").exists())

    def test_install_store_rejects_bad_appid(self):
        with self.assertRaises(ValueError):
            state.InstallStore(Path("/tmp")).get("../../etc")


if __name__ == "__main__":
    unittest.main()
