import importlib.util
import json
import sys
import tempfile
import types
import unittest
import zipfile
import tarfile
from pathlib import Path

from backend.decision import DecisionTree


ROOT = Path(__file__).resolve().parents[1]


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def load_main_module(temp_home: Path):
    decky = types.SimpleNamespace(
        HOME=str(temp_home),
        USER="deck",
        DECKY_USER="deck",
        DECKY_USER_HOME=str(temp_home),
        DECKY_HOME=str(temp_home / "homebrew"),
        DECKY_PLUGIN_DIR=str(ROOT),
        logger=FakeLogger(),
    )
    sys.modules["decky"] = decky
    module_name = "decky_renodx_main_for_tests"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.decky = decky
    return module


class BackendMockTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.module = load_main_module(self.home)

    def tearDown(self):
        self.temp.cleanup()

    async def test_version_comparison_is_semantic(self):
        plugin = self.module.Plugin()
        self.assertTrue(plugin._is_newer_version("0.1.9", "0.1.10"))
        self.assertFalse(plugin._is_newer_version("0.1.10", "0.1.9"))
        self.assertFalse(plugin._is_newer_version("0.1.10", "0.1.10"))

    async def test_current_version_falls_back_to_module_package_json(self):
        plugin = self.module.Plugin()
        self.module.decky.DECKY_PLUGIN_DIR = str(self.home / "missing-plugin")

        self.assertNotEqual(plugin._current_version(), "unknown")

    async def test_release_asset_requires_decky_renodx_zip(self):
        plugin = self.module.Plugin()
        release = {
            "assets": [
                {"name": "notes.txt", "browser_download_url": "nope"},
                {"name": "decky-renodx.zip", "browser_download_url": "ok"},
            ]
        }
        self.assertEqual(plugin._release_asset(release)["browser_download_url"], "ok")

    async def test_safe_extract_rejects_path_traversal(self):
        plugin = self.module.Plugin()
        archive_path = self.home / "bad.zip"
        extract_dir = self.home / "extract"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.txt", "bad")

        with zipfile.ZipFile(archive_path) as archive:
            with self.assertRaises(ValueError):
                plugin._safe_extract(archive, extract_dir)

    async def test_validate_extracted_plugin_accepts_expected_manifest(self):
        plugin = self.module.Plugin()
        plugin_dir = self.home / "decky-renodx"
        (plugin_dir / "dist").mkdir(parents=True)
        (plugin_dir / "backend").mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "Decky RenoDX"}), encoding="utf-8")
        (plugin_dir / "package.json").write_text(json.dumps({"name": "decky-renodx", "version": "0.1.0"}), encoding="utf-8")
        (plugin_dir / "dist" / "index.js").write_text("// ok", encoding="utf-8")
        (plugin_dir / "main.py").write_text("# ok", encoding="utf-8")
        (plugin_dir / "backend" / "__init__.py").write_text("# ok", encoding="utf-8")
        (plugin_dir / "backend" / "cache.py").write_text("# ok", encoding="utf-8")

        plugin._validate_extracted_plugin(plugin_dir)

    async def test_import_renodx_copies_addon_to_selected_executable_dir(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "Game"
        game_dir.mkdir()
        exe = game_dir / "Game.exe"
        exe.write_text("", encoding="utf-8")
        addon = self.home / "renodx-test.addon64"
        addon.write_text("addon", encoding="utf-8")

        result = await plugin.import_renodx_for_game("123", str(addon), str(exe))

        self.assertEqual(result["status"], "success")
        self.assertTrue((game_dir / "renodx-test.addon64").exists())
        self.assertIn("DXVK_HDR=1", result["launch_options"])

    async def test_renodx_mod_parser_and_matcher(self):
        plugin = self.module.Plugin()
        markdown = """
        # List
        | Name | Maintainer | Links | Status |
        | --- | --- | --- | --- |
        | Bayonetta | ShortFuse | [![Snapshot](badge)](https://example.com/renodx-bayonetta.addon32) | :white_check_mark: |
        | Other Game | Dev | [![Nexus Mods](badge)](https://www.nexusmods.com/other/mods/1) | :construction: |
        """

        mods = plugin._parse_renodx_mods(markdown)
        match = plugin._match_renodx_mod("Bayonetta", mods)

        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Bayonetta")
        self.assertEqual(match["status"], "working")
        self.assertEqual(match["snapshotLinks"], ["https://example.com/renodx-bayonetta.addon32"])
        self.assertEqual(match["addon_url"], "https://example.com/renodx-bayonetta.addon32")
        self.assertEqual(match["source_type"], "snapshot")
        self.assertEqual(match["bitness"], "32")

    async def test_renodx_parser_matches_alien_isolation_from_wiki_row(self):
        plugin = self.module.Plugin()
        markdown = (
            '| Name | Maintainer | Links | Status | '
            '| Alien: Isolation | Musa | [![Nexus Mods](badge)](https://www.nexusmods.com/alienisolation/mods/78) '
            '· [![Snapshot](badge)](https://github.com/mqhaji/renodx/releases/download/snapshot/renodx-alienisolation.addon32) '
            '| :construction: |'
        )

        mods = plugin._parse_renodx_mods(markdown)
        match = plugin._match_renodx_mod("Alien Isolation", mods)

        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Alien: Isolation")
        self.assertEqual(match["status"], "in_progress")
        self.assertIn("https://github.com/mqhaji/renodx/releases/download/snapshot/renodx-alienisolation.addon32", match["snapshotLinks"])

    async def test_renodx_parser_supports_multi_game_engine_sections(self):
        plugin = self.module.Plugin()
        markdown = """
        # List
        | Name | Maintainer | Links | Status |
        | --- | --- | --- | --- |
        | Exact Game | Dev | [![Snapshot](badge)](https://example.com/exact.addon64) | :white_check_mark: |

        ## Multi-Game Mods
        ### Unreal Engine [![Snapshot](badge)](https://example.com/renodx-unrealengine.addon64)
        | Name | Status | Notes |
        | --- | --- | --- |
        | Sand Land | :white_check_mark: | Use output size upgrade. |
        """

        mods = plugin._parse_renodx_mods(markdown)
        listed = plugin._match_renodx_mod("SAND LAND", mods)
        generic = plugin._match_renodx_mod("Unknown Unreal Game", mods, "Unreal Engine 5")

        self.assertEqual(listed["match_type"], "generic_listed")
        self.assertEqual(listed["engine_bucket"], "unreal")
        self.assertEqual(listed["addon_url"], "https://example.com/renodx-unrealengine.addon64")
        self.assertEqual(generic["match_type"], "generic_engine")
        self.assertTrue(generic["experimental"])

    async def test_renodx_parser_marks_manual_sources(self):
        plugin = self.module.Plugin()
        markdown = """
        # List
        | Name | Maintainer | Links | Status |
        | --- | --- | --- | --- |
        | Manual Game | Dev | [![Nexus Mods](badge)](https://www.nexusmods.com/game/mods/1) | :white_check_mark: |
        """

        match = plugin._match_renodx_mod("Manual Game", plugin._parse_renodx_mods(markdown))

        self.assertEqual(match["source_type"], "nexus")
        self.assertEqual(match["addon_url"], "")
        self.assertEqual(match["manual_url"], "https://www.nexusmods.com/game/mods/1")

    async def test_list_installed_games_parses_steam_libraries(self):
        plugin = self.module.Plugin()
        steamapps = self.home / ".local" / "share" / "Steam" / "steamapps"
        steamapps.mkdir(parents=True)
        (steamapps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + str(self.home / ".local" / "share" / "Steam").replace("\\", "\\\\") + '"\n\t}\n}\n',
            encoding="utf-8",
        )
        (steamapps / "appmanifest_123.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"123"\n\t"name"\t\t"Example Game"\n\t"installdir"\t\t"Example Game"\n}\n',
            encoding="utf-8",
        )

        result = await plugin.list_installed_games()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["games"], [{"appid": "123", "name": "Example Game", "source": "steam"}])

    async def test_find_game_path_uses_deck_user_home_when_decky_home_is_root(self):
        self.module.decky.HOME = "/root"
        plugin = self.module.Plugin()
        steamapps = self.home / ".local" / "share" / "Steam" / "steamapps"
        game_dir = steamapps / "common" / "Example Game"
        steamapps.mkdir(parents=True)
        game_dir.mkdir(parents=True)
        (steamapps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + str(self.home / ".local" / "share" / "Steam").replace("\\", "\\\\") + '"\n\t}\n}\n',
            encoding="utf-8",
        )
        (steamapps / "appmanifest_123.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"123"\n\t"name"\t\t"Example Game"\n\t"installdir"\t\t"Example Game"\n}\n',
            encoding="utf-8",
        )

        self.assertEqual(plugin._find_game_path("123"), str(game_dir))

    async def test_root_home_does_not_move_runtime_to_root(self):
        self.module.decky.HOME = "/root"
        plugin = self.module.Plugin()

        self.assertEqual(plugin.environment["HOME"], str(self.home))
        self.assertTrue(plugin.main_path.startswith(str(self.home)))
        self.assertNotIn("/root", plugin.main_path)

    async def test_decky_user_home_takes_priority_over_effective_home(self):
        self.module.decky.HOME = "/root"
        self.module.decky.DECKY_USER = "yuri"
        self.module.decky.DECKY_USER_HOME = str(self.home)
        plugin = self.module.Plugin()

        self.assertEqual(plugin.environment["USER"], "yuri")
        self.assertEqual(plugin.environment["HOME"], str(self.home))

    async def test_reshade_ini_skips_tutorial(self):
        plugin = self.module.Plugin()
        ini = self.home / "ReShade.ini"
        ini.write_text("[GENERAL]\nEffectSearchPaths=.\n", encoding="utf-8")

        plugin._ensure_reshade_tutorial_skipped(ini)

        self.assertIn("TutorialProgress=4", ini.read_text(encoding="utf-8"))

    async def test_existing_runtime_ini_is_migrated_on_init(self):
        runtime = self.home / ".local" / "share" / "decky-renodx" / "reshade"
        runtime.mkdir(parents=True)
        (runtime / "ReShade.ini").write_text(
            "[GENERAL]\n"
            "EffectSearchPaths=.local\\share\\decky-renodx\\reshade\\ReShade_shaders\\Merged\\Shaders\n"
            "TextureSearchPaths=.local\\share\\decky-renodx\\reshade\\ReShade_shaders\\Merged\\Textures\n"
            "PresetPath=.local\\share\\decky-renodx\\reshade\\ReShadePreset.ini\n",
            encoding="utf-8",
        )

        self.module.Plugin()

        text = (runtime / "ReShade.ini").read_text(encoding="utf-8")
        self.assertIn("TutorialProgress=4", text)
        self.assertIn("EffectSearchPaths=.\\ReShade_shaders\\Merged\\Shaders", text)
        self.assertIn("TextureSearchPaths=.\\ReShade_shaders\\Merged\\Textures", text)
        self.assertIn("PresetPath=.\\ReShadePreset.ini", text)
        self.assertNotIn(".local\\share", text)

    async def test_autohdr_payload_keeps_reshade_addon_names(self):
        plugin = self.module.Plugin()
        bin_dir = self.home / "bin"
        main_path = self.home / "runtime"
        bin_dir.mkdir()
        (main_path / "AutoHDR_addons").mkdir(parents=True)
        (main_path / "ReShade_shaders" / "Merged" / "Shaders").mkdir(parents=True)
        (main_path / "ReShade_shaders" / "Merged" / "Textures").mkdir(parents=True)
        source = self.home / "payload"
        source.mkdir()
        (source / "AutoHDR32.addon").write_text("32", encoding="utf-8")
        (source / "AutoHDR64.addon").write_text("64", encoding="utf-8")
        (source / "AutoHDR.fx").write_text("fx", encoding="utf-8")
        (bin_dir / "ReShade.fxh").write_text("fxh", encoding="utf-8")
        with tarfile.open(bin_dir / "autohdr_addon.tar.gz", "w:gz") as archive:
            archive.add(source / "AutoHDR32.addon", arcname="AutoHDR32.addon")
            archive.add(source / "AutoHDR64.addon", arcname="AutoHDR64.addon")
        with tarfile.open(bin_dir / "advanced_autohdr_effect.tar.gz", "w:gz") as archive:
            archive.add(source / "AutoHDR.fx", arcname="Shaders/AutoHDR.fx")

        plugin._install_autohdr_payloads(main_path, bin_dir)

        for name in ["AutoHDR32.addon", "AutoHDR64.addon", "AutoHDR.addon32", "AutoHDR.addon64"]:
            self.assertTrue((main_path / "AutoHDR_addons" / name).exists())
        self.assertTrue((main_path / "ReShade_shaders" / "Merged" / "Shaders" / "ReShade.fxh").exists())

    async def test_specialk_install_copies_hook_and_ini(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        runtime = Path(plugin.main_path) / "SpecialK" / "x64"
        game_dir.mkdir()
        runtime.mkdir(parents=True)
        (runtime / "SpecialK64.dll").write_text("dll", encoding="utf-8")

        result = plugin._install_specialk_for_game(game_dir, "dxgi", "64")

        self.assertEqual(result["status"], "success")
        self.assertTrue((game_dir / "dxgi.dll").exists())
        specialk_ini = (game_dir / "SpecialK.ini").read_text(encoding="utf-8")
        dxgi_ini = (game_dir / "dxgi.ini").read_text(encoding="utf-8")
        self.assertIn("UsingWINE=true", specialk_ini)
        self.assertIn("HDR.Enable=true", specialk_ini)
        self.assertIn("Use16BitSwapChain=true", dxgi_ini)

    async def test_backend_specialk_installer_does_not_disable_steamapi(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        runtime = self.home / "SpecialK64.dll"
        exe = game_dir / "Game.exe"
        game_dir.mkdir()
        runtime.write_text("dll", encoding="utf-8")
        exe.write_text("exe", encoding="utf-8")
        (game_dir / "dxgi.ini").write_text("stale", encoding="utf-8")
        (game_dir / "SpecialK.ini").write_text("[Steam.System]\nEnable=false\n\n[Steam.Log]\nSilent=true\n", encoding="utf-8")

        success, message = plugin.installer.install_special_k("123", str(exe), str(runtime), delay_seconds="5")

        self.assertTrue(success, message)
        specialk_ini = (game_dir / "SpecialK.ini").read_text(encoding="utf-8")
        self.assertIn("usingwine = true", specialk_ini.lower())
        self.assertIn("injectiondelay = 5", specialk_ini.lower())
        self.assertNotIn("[Steam.System]", specialk_ini)
        self.assertNotIn("[Steam.Log]", specialk_ini)
        self.assertFalse((game_dir / "dxgi.ini").exists())

    async def test_specialk_widget_repair_backs_up_imgui_state(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        game_dir.mkdir()
        (game_dir / "imgui.ini").write_text("old-window-state", encoding="utf-8")

        backed_up = plugin._reset_specialk_imgui_state(game_dir)

        self.assertEqual(backed_up, 1)
        self.assertFalse((game_dir / "imgui.ini").exists())
        self.assertTrue(list(game_dir.glob("imgui.ini.decky-renodx-backup-*")))

    async def test_game_hdr_status_reports_installed_and_update_needed(self):
        plugin = self.module.Plugin()
        plugin._current_version = lambda: "0.2.0"
        game_dir = self.home / "game"
        game_dir.mkdir()
        (game_dir / "dxgi.dll").write_text("dll", encoding="utf-8")
        (game_dir / "dxgi.ini").write_text("[SpecialK.HDR]\nHDR.Enable=true\n", encoding="utf-8")
        (game_dir / ".decky-renodx-hdr.json").write_text(
            json.dumps({"method": "specialk", "plugin_version": "0.1.0"}),
            encoding="utf-8",
        )
        plugin._resolve_game_exe_dir = lambda _appid, _selected="": game_dir

        result = await plugin.get_game_hdr_status("123")

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["installed"])
        self.assertEqual(result["method"], "specialk")
        self.assertTrue(result["needs_update"])

    async def test_decision_tree_uses_reshade_when_api_unknown(self):
        recommendations = DecisionTree().evaluate({
            "appid": "123",
            "title": "Unknown Game",
            "graphics_api": "unknown",
            "anti_cheat": [],
            "is_multiplayer": False,
            "native_hdr": "unknown",
            "special_k_wiki": False,
        })

        self.assertEqual(recommendations[0]["method"], "reshade")
        self.assertEqual(recommendations[0]["score"], 50)

    async def test_decision_tree_skips_special_k_for_dx9_without_exact_support(self):
        recommendations = DecisionTree().evaluate({
            "appid": "460790",
            "title": "Bayonetta",
            "graphics_api": "dx9",
            "anti_cheat": [],
            "is_multiplayer": False,
            "native_hdr": "unknown",
            "special_k_wiki": False,
        })

        self.assertNotIn("special_k", [item["method"] for item in recommendations])
        self.assertEqual(recommendations[0]["method"], "reshade")

    async def test_decision_tree_allows_special_k_attempt_for_dx11_family(self):
        recommendations = DecisionTree().evaluate({
            "appid": "999",
            "title": "Known API Game",
            "graphics_api": "dx11_dx12",
            "anti_cheat": [],
            "is_multiplayer": False,
            "native_hdr": "unknown",
            "special_k_wiki": False,
        })

        self.assertEqual(recommendations[0]["method"], "special_k")
        self.assertTrue(recommendations[0]["requires_verification"])
        self.assertEqual(recommendations[1]["method"], "reshade")

    async def test_decision_tree_allows_special_k_attempt_for_dxgi_hook(self):
        recommendations = DecisionTree().evaluate({
            "appid": "999",
            "title": "DXGI Hook Game",
            "graphics_api": "dxgi",
            "anti_cheat": [],
            "is_multiplayer": False,
            "native_hdr": "unknown",
            "special_k_wiki": False,
        })

        self.assertEqual(recommendations[0]["method"], "special_k")
        self.assertTrue(recommendations[0]["requires_verification"])

    async def test_api_detection_scans_unity_player_imports(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "unity-game"
        game_dir.mkdir()
        (game_dir / "Game.exe").write_bytes(b"Unity bootstrap")
        (game_dir / "UnityPlayer.dll").write_bytes(b"noise D3D11.dll more noise")
        plugin._detect_api_with_letmereshade_script = lambda _path: {"status": "error", "message": "skip script"}

        result = await plugin._detect_api_for_path(str(game_dir / "Game.exe"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dx11_dx12")
        self.assertEqual(result["injection_dll"], "dxgi")
        self.assertEqual(result["engine"], "unity")

    async def test_api_detection_uses_specific_letmereshade_detector_result(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        game_dir.mkdir()
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "d3d9",
            "architecture": "64",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(game_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "d3d9")
        self.assertEqual(result["detector"], "letmereshade")

    async def test_api_detection_refines_generic_dxgi_script_hint(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        game_dir.mkdir()
        (game_dir / "Game.exe").write_bytes(b"launcher imports D3D11.dll")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(game_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "d3d11")
        self.assertEqual(result["script_api_hint"], "dxgi")

    async def test_api_detection_keeps_letmereshade_dxgi_default_when_refinement_fails(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        game_dir.mkdir()
        (game_dir / "Game.exe").write_bytes(b"packed executable without readable imports")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(game_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dxgi")
        self.assertEqual(result["confidence"], "hook-default")

    async def test_api_requirement_text_detects_directx_11(self):
        plugin = self.module.Plugin()

        self.assertEqual(plugin._api_from_requirement_text("DirectX: Version 11"), "d3d11")
        self.assertEqual(plugin._api_from_requirement_text("<strong>DirectX:</strong> Version 12"), "d3d12")

    async def test_pcgamingwiki_api_table_maps_graphics_api(self):
        scraper = self.module.PCGamingWikiScraper()

        result = scraper._parse_api_data({
            "cargoquery": [{
                "title": {
                    "Page": "Example",
                    "Direct3D versions": "11, 12",
                    "OpenGL versions": "",
                    "Vulkan versions": "",
                }
            }]
        })

        self.assertEqual(result["api"], "d3d12")
        self.assertEqual(result["api_source"], "pcgamingwiki_api_table")

    async def test_pcgamingwiki_api_table_maps_opengl_when_no_direct3d(self):
        scraper = self.module.PCGamingWikiScraper()

        result = scraper._parse_api_data({
            "cargoquery": [{
                "title": {
                    "Page": "KOTOR2",
                    "Direct3D versions": "",
                    "OpenGL versions": "1.4",
                    "Vulkan versions": "",
                }
            }]
        })

        self.assertEqual(result["api"], "opengl32")

    async def test_recommendation_uses_steam_metadata_api_fallback(self):
        plugin = self.module.Plugin()
        async def fake_detect_api(_path, _logger=None):
            return {"status": "success", "api": "unknown"}
        async def fake_metadata_api(_appid, _logger=None):
            return {
                "status": "success",
                "api": "d3d11",
                "injection_dll": "dxgi",
                "engine": "unknown",
                "confidence": "metadata",
                "source": "steam_appdetails",
            }
        async def fake_renodx(_title):
            return {"status": "success", "supported": False}
        plugin._detect_api_with_cache = fake_detect_api
        plugin._detect_api_from_steam_metadata = fake_metadata_api
        plugin.wiki_scraper.get_game_data = lambda _appid: {"status": "error"}
        plugin.check_renodx_support = fake_renodx
        game_dir = self.home / "Ni no Kuni"
        game_dir.mkdir()

        result = await plugin.get_hdr_recommendation("798460", "Ni no Kuni Wrath of the White Witch Remastered", str(game_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["context"]["graphics_api"], "d3d11")
        self.assertEqual(result["context"]["architecture"], "unknown")
        self.assertEqual(result["recommendations"][0]["method"], "special_k")

    async def test_recommendation_resolves_executable_when_frontend_path_is_empty(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "game"
        exe = game_dir / "Game.exe"
        game_dir.mkdir()
        exe.write_bytes(b"imports d3d11.dll")
        async def fake_find(_appid):
            return {
                "status": "success",
                "steam_logs_result": {"status": "not_found"},
                "enhanced_detection_result": {"status": "success", "executable_path": str(exe)},
            }
        async def fake_renodx(_title):
            return {"status": "success", "supported": False}
        plugin.find_game_executable_path = fake_find
        plugin.check_renodx_support = fake_renodx
        plugin.wiki_scraper.get_game_data = lambda _appid: {"status": "error"}
        plugin._detect_api_with_letmereshade_script = lambda _path: {"status": "error", "message": "skip"}

        result = await plugin.get_hdr_recommendation("123", "Example Game", "")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["context"]["graphics_api"], "d3d11")
        self.assertEqual(result["context"]["architecture"], "64")
        self.assertEqual(result["recommendations"][0]["method"], "special_k")

    async def test_steam_metadata_uses_relaxed_json_fetch(self):
        plugin = self.module.Plugin()
        seen = []
        plugin._fetch_json = lambda url: seen.append(url) or {
            "798460": {
                "success": True,
                "data": {"pc_requirements": {"minimum": "DirectX: Version 11"}},
            }
        }

        result = plugin._detect_api_from_steam_metadata_sync("798460")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "d3d11")
        self.assertIn("store.steampowered.com", seen[0])

    async def test_unreal_dxgi_detection_becomes_dx11_dx12_family(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "unreal-game"
        exe = game_dir / "Project" / "Binaries" / "Win64" / "Game-Win64-Shipping.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"unreal bootstrap")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(game_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dx11_dx12")
        self.assertEqual(result["injection_dll"], "dxgi")
        self.assertEqual(result["engine"], "unreal")

    async def test_unreal_engine_detects_from_binaries_win64_directory(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "SandLike"
        exe_dir = game_dir / "SandLike" / "Binaries" / "Win64"
        exe = exe_dir / "SandLike-Win64-Shipping.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"unreal shipping")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(exe_dir))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dx11_dx12")
        self.assertEqual(result["engine"], "unreal")

    async def test_unity_engine_detects_from_parent_directory(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "UnityLike"
        nested = game_dir / "Bin" / "Win64"
        nested.mkdir(parents=True)
        (game_dir / "UnityPlayer.dll").write_bytes(b"unity")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(nested))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dx11_dx12")
        self.assertEqual(result["engine"], "unity")

    async def test_unity_engine_detects_from_child_directory(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "UnityLike"
        nested = game_dir / "Bin" / "Win64"
        nested.mkdir(parents=True)
        (nested / "Game.exe").write_bytes(b"launcher")
        (nested / "Runtime" / "UnityPlayer.dll").parent.mkdir()
        (nested / "Runtime" / "UnityPlayer.dll").write_bytes(b"unity")
        plugin._detect_api_with_letmereshade_script = lambda _path: {
            "status": "success",
            "api": "dxgi",
            "architecture": "64",
            "injection_dll": "dxgi",
            "detector": "letmereshade",
        }

        result = await plugin._detect_api_for_path(str(nested))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["api"], "dx11_dx12")
        self.assertEqual(result["engine"], "unity")

    async def test_engine_detection_does_not_scan_sibling_games_from_common_parent(self):
        plugin = self.module.Plugin()
        common = self.home / "steamapps" / "common"
        bayonetta = common / "Bayonetta"
        sibling = common / "SANDLAND" / "SANDLAND" / "Binaries" / "Win64"
        bayonetta.mkdir(parents=True)
        sibling.mkdir(parents=True)
        (bayonetta / "Bayonetta.exe").write_bytes(b"bayonetta")
        (sibling / "SANDLAND-Win64-Shipping.exe").write_bytes(b"unreal")

        self.assertEqual(plugin._detect_engine_family(bayonetta / "Bayonetta.exe"), "unknown")

    async def test_unknown_api_is_not_cached(self):
        cache = self.module.PersistentCache(str(self.home / "cache.json"))
        game_dir = self.home / "game"
        game_dir.mkdir()

        cache.set_api_info(str(game_dir), {"status": "success", "api": "unknown"})

        self.assertIsNone(cache.get_api_info(str(game_dir)))

    async def test_cached_metadata_does_not_preserve_unknown_api(self):
        cache = self.module.PersistentCache(str(self.home / "cache.json"))

        cache.set_game_metadata("123", {"graphics_api": "unknown", "native_hdr": "unknown"})

        self.assertNotIn("graphics_api", cache.get_game_metadata("123"))

    async def test_old_metadata_schema_is_ignored(self):
        cache = self.module.PersistentCache(str(self.home / "cache.json"))
        cache.set("metadata_123", {"graphics_api": "unknown", "renodx_supported": False})

        self.assertIsNone(cache.get_game_metadata("123"))

    async def test_reset_plugin_caches_clears_detection_and_metadata(self):
        plugin = self.module.Plugin()
        plugin.executable_cache["x"] = {"status": "success"}
        plugin.persistent_cache.set("foo", "bar")
        renodx_cache = Path(plugin.main_path) / "renodx_mods_cache.json"
        renodx_cache.write_text("{}", encoding="utf-8")

        result = await plugin.reset_plugin_caches()

        self.assertEqual(result["status"], "success")
        self.assertEqual(plugin.executable_cache, {})
        self.assertEqual(plugin.persistent_cache.data, {})
        self.assertFalse(renodx_cache.exists())

    async def test_special_k_verified_override_promotes_special_k(self):
        plugin = self.module.Plugin()
        await plugin.set_special_k_verified("123", True)
        plugin.persistent_cache.set_game_metadata_value("123", "graphics_api", "dx11_dx12")

        result = await plugin.get_hdr_recommendation("123", "Known Working SK Game", "")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["recommendations"][0]["method"], "special_k")

    async def test_recommendation_refreshes_renodx_after_engine_detection(self):
        plugin = self.module.Plugin()
        game_dir = self.home / ".local" / "share" / "Steam" / "steamapps" / "common" / "Sand Land" / "SandLand" / "Binaries" / "Win64"
        game_dir.mkdir(parents=True)
        exe = game_dir / "SandLand-Win64-Shipping.exe"
        exe.write_bytes(b"MZ")
        plugin.wiki_scraper.get_game_data = lambda _appid: {"native_hdr": "unknown", "graphics_api": "unknown", "special_k_compatible": False}
        async def fake_detect(_path, _logger=None):
            return {"status": "success", "api": "dxgi", "architecture": "64", "injection_dll": "dxgi", "engine": "unreal"}
        plugin._detect_api_with_cache = fake_detect

        async def fake_renodx(_title, engine=""):
            if engine == "unreal":
                return {"status": "success", "supported": True, "match": {"name": "Sand Land", "match_type": "generic_listed", "source_type": "generic", "addon_url": "https://example.com/renodx-unrealengine.addon64"}}
            return {"status": "success", "supported": False}
        plugin.check_renodx_support = fake_renodx

        result = await plugin.get_hdr_recommendation("1979440", "SAND LAND", str(exe))

        self.assertEqual(result["context"]["engine"], "unreal")
        self.assertTrue(result["context"]["renodx_supported"])
        self.assertEqual(result["recommendations"][0]["method"], "renodx")
        self.assertNotIn("special_k", [item["method"] for item in result["recommendations"]])

    async def test_manual_renodx_keeps_special_k_as_lazy_option(self):
        tree = self.module.DecisionTree()

        recommendations = tree.evaluate({
            "appid": "123",
            "title": "Manual RenoDX Game",
            "graphics_api": "d3d11",
            "anti_cheat": [],
            "native_hdr": "unknown",
            "renodx_supported": True,
            "renodx_flow_enabled": True,
            "renodx_match": {
                "name": "Manual RenoDX Game",
                "match_type": "specific",
                "source_type": "nexus",
                "manual_url": "https://www.nexusmods.com/game/mods/1",
                "addon_url": "",
            },
        })

        methods = [item["method"] for item in recommendations]
        self.assertEqual(methods[0], "renodx")
        self.assertIn("special_k", methods)

    async def test_renodx_install_resolves_executable_before_install(self):
        plugin = self.module.Plugin()
        base = self.home / ".local" / "share" / "Steam" / "steamapps" / "common" / "SANDLAND"
        exe_dir = base / "SANDLAND" / "Binaries" / "Win64"
        exe_dir.mkdir(parents=True)
        exe = exe_dir / "SANDLAND-Win64-Shipping.exe"
        exe.write_bytes(b"MZ")
        addon = self.home / "renodx-unrealengine.addon64"
        addon.write_text("addon", encoding="utf-8")
        called = {}

        async def fake_resolve(_appid, _logger=None):
            return str(exe)
        async def fake_manage(_appid, _action, _api, _vulkan, selected_executable_path):
            called["selected_executable_path"] = selected_executable_path
            (exe_dir / "dxgi.dll").write_text("dll", encoding="utf-8")
            return {"status": "success", "injection_dll": "dxgi", "launch_options": "opts"}
        plugin._resolve_game_executable_for_recommendation = fake_resolve
        plugin.manage_game_reshade = fake_manage
        plugin._download_url = lambda _url, target: target.write_text("addon", encoding="utf-8")
        async def fake_set_launch(_appid, _opts):
            return None
        plugin._set_steam_launch_options = fake_set_launch

        result = await plugin._install_renodx_mod_for_game("1979440", "SAND LAND", "", {"name": "Sand Land", "addon_url": "https://example.com/renodx-unrealengine.addon64"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(called["selected_executable_path"], str(exe))
        self.assertTrue((exe_dir / "renodx-unrealengine.addon64").exists())

    async def test_renodx_install_removes_fallback_effects(self):
        plugin = self.module.Plugin()
        exe_dir = self.home / "Game" / "Binaries" / "Win64"
        exe_dir.mkdir(parents=True)
        for name in ["AutoHDR64.addon", "AutoHDR.addon64", "AdvancedAutoHDR.fx", "lilium__hdr.fx"]:
            (exe_dir / name).write_text("effect", encoding="utf-8")
        (exe_dir / "ReShade_shaders").mkdir()
        (exe_dir / "ReShadePreset.ini").write_text("Techniques=AutoHDR\n", encoding="utf-8")
        (exe_dir / "ReShade.ini").write_text("[GENERAL]\nEffectSearchPaths=.\\ReShade_shaders\\Merged\\Shaders\n", encoding="utf-8")

        removed = plugin._configure_renodx_only_install(exe_dir, "renodx-unrealengine.addon64")

        for name in ["AutoHDR64.addon", "AutoHDR.addon64", "AdvancedAutoHDR.fx", "lilium__hdr.fx", "ReShade_shaders"]:
            self.assertFalse((exe_dir / name).exists())
            self.assertIn(name, removed)
        self.assertIn("Techniques=", (exe_dir / "ReShadePreset.ini").read_text(encoding="utf-8"))
        ini = (exe_dir / "ReShade.ini").read_text(encoding="utf-8")
        self.assertIn("EffectSearchPaths=", ini)
        self.assertIn("LoadFromDllMain=renodx-unrealengine.addon64", ini)

    async def test_renodx_support_is_blocked_while_flow_disabled(self):
        tree = self.module.DecisionTree()
        recommendations = tree.evaluate({
            "appid": "123",
            "title": "RenoDX Game",
            "graphics_api": "d3d11",
            "anti_cheat": [],
            "native_hdr": "unknown",
            "renodx_supported": True,
            "renodx_flow_enabled": False,
        })

        methods = [recommendation["method"] for recommendation in recommendations]

        self.assertNotEqual(recommendations[0]["method"], "renodx")
        self.assertIn("renodx_disabled", methods)

    async def test_surgical_uninstall_fallback_removes_marker_hdr_files(self):
        plugin = self.module.Plugin()
        game_dir = self.home / "steamapps" / "common" / "Game"
        game_dir.mkdir(parents=True)
        exe = game_dir / "Game.exe"
        exe.write_text("exe", encoding="utf-8")
        for name in [".decky-renodx-hdr.json", "dxgi.dll", "dxgi.ini", "SpecialK.ini", "ReShade.ini"]:
            (game_dir / name).write_text("hdr", encoding="utf-8")

        result = await plugin.run_surgical_uninstall("123", str(exe))

        self.assertEqual(result["status"], "success")
        self.assertFalse((game_dir / "dxgi.dll").exists())
        self.assertFalse((game_dir / "SpecialK.ini").exists())
        self.assertFalse((game_dir / ".decky-renodx-hdr.json").exists())

    async def test_clear_steam_compatdata_removes_only_appid_prefix(self):
        plugin = self.module.Plugin()
        compat = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / "123"
        other = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / "456"
        (compat / "pfx").mkdir(parents=True)
        (other / "pfx").mkdir(parents=True)

        result = plugin._clear_steam_compatdata("123")

        self.assertEqual(result["status"], "success")
        self.assertFalse(compat.exists())
        self.assertTrue(other.exists())

    async def test_clear_steam_compatdata_rejects_invalid_appid(self):
        plugin = self.module.Plugin()

        result = plugin._clear_steam_compatdata("../123")

        self.assertEqual(result["status"], "error")
        self.assertIn("invalid appid", result["message"])

    async def test_hdr_launch_options_support_multiple_native_overrides(self):
        plugin = self.module.Plugin()

        result = plugin._hdr_launch_options("d3d9;dxgi")

        self.assertIn("d3d9=n,b", result)
        self.assertIn("dxgi=n,b", result)
        self.assertIn("d3dcompiler_47=n", result)

    async def test_hdr_launch_options_for_opengl_do_not_force_d3dcompiler(self):
        plugin = self.module.Plugin()

        result = plugin._hdr_launch_options("opengl32")

        self.assertNotIn("WINEDLLOVERRIDES", result)
        self.assertNotIn("d3d9=n,b", result)
        self.assertNotIn("d3dcompiler_47=n", result)

    async def test_wine_dll_override_is_written_to_compatdata_user_reg(self):
        plugin = self.module.Plugin()
        compat = self.home / ".steam" / "steam" / "steamapps" / "compatdata" / "208580"

        result = plugin._set_wine_dll_override("208580", "opengl32")

        user_reg = compat / "pfx" / "user.reg"
        self.assertEqual(result["wine_dll_overrides"], {"opengl32": "native,builtin"})
        self.assertIn(str(user_reg), result["modified_files"])
        self.assertIn('[Software\\\\Wine\\\\DllOverrides]', user_reg.read_text(encoding="utf-8"))
        self.assertIn('"opengl32"="native,builtin"', user_reg.read_text(encoding="utf-8"))

    async def test_wine_dll_override_backs_up_existing_user_reg_and_cleans_bad_section(self):
        plugin = self.module.Plugin()
        user_reg = self.home / ".steam" / "steam" / "steamapps" / "compatdata" / "208580" / "pfx" / "user.reg"
        user_reg.parent.mkdir(parents=True)
        user_reg.write_text(
            'WINE REGISTRY Version 2\n\n[Software\\Wine\\DllOverrides]\n"opengl32"="builtin"\n\n[Other]\n"key"="value"\n',
            encoding="utf-8",
        )

        result = plugin._set_wine_dll_override("208580", "opengl32")
        text = user_reg.read_text(encoding="utf-8")

        self.assertIn(str(user_reg), result["backups"])
        self.assertTrue(Path(result["backups"][str(user_reg)]).exists())
        self.assertNotIn("[Software\\Wine\\DllOverrides]", text)
        self.assertIn('[Software\\\\Wine\\\\DllOverrides]', text)
        self.assertIn('"opengl32"="native,builtin"', text)
        self.assertIn("[Other]", text)

    async def test_parse_reshade_selected_api_ignores_d3dcompiler(self):
        plugin = self.module.Plugin()

        result = plugin._parse_reshade_selected_api(
            'Use this launch option: WINEDLLOVERRIDES="d3dcompiler_47=n;opengl32=n,b" %command%'
        )

        self.assertEqual(result, "opengl32")

    async def test_replace_localconfig_launch_options_targets_launch_block(self):
        plugin = self.module.Plugin()
        text = '''
        "208580"
        {
            "LastPlayed" "1"
            "LaunchOptions" "WINEDLLOVERRIDES=\\"d3d9=n,b\\" %command%"
        }
        '''

        updated, changed = plugin._replace_localconfig_launch_options(
            text,
            "208580",
            'WINEDLLOVERRIDES=\\"opengl32=n,b\\" %command%',
        )

        self.assertTrue(changed)
        self.assertIn("opengl32=n,b", updated)
        self.assertNotIn("d3d9=n,b", updated)

    async def test_replace_localconfig_launch_options_inserts_missing_key(self):
        plugin = self.module.Plugin()
        text = '''
        "208580"
        {
            "LastPlayed" "1"
        }
        '''

        updated, changed = plugin._replace_localconfig_launch_options(
            text,
            "208580",
            'WINEDLLOVERRIDES=\\"opengl32=n,b\\" %command%',
        )

        self.assertTrue(changed)
        self.assertIn('"LaunchOptions"', updated)
        self.assertIn("opengl32=n,b", updated)

    async def test_dx9_recommendation_does_not_offer_special_k_wrapper_path(self):
        tree = self.module.DecisionTree()

        result = tree.evaluate({"appid": "123", "graphics_api": "d3d9", "anti_cheat": []})
        methods = [item["method"] for item in result]

        self.assertNotIn("special_k", methods)
        self.assertIn("reshade", methods)

    async def test_restart_uses_helper_when_systemd_run_fails(self):
        plugin = self.module.Plugin()
        calls = []

        original_popen = self.module.subprocess.Popen

        def fake_popen(argv, **kwargs):
            calls.append(argv)
            # Simulate systemd-run failing by raising an exception
            if argv[0] == "systemd-run":
                raise OSError("fail")
            # Simulate helper script succeeding
            return None

        self.module.subprocess.Popen = fake_popen
        try:
            result = plugin._schedule_loader_restart("test")
        finally:
            self.module.subprocess.Popen = original_popen

        self.assertTrue(result["scheduled"])
        self.assertEqual(result["method"], "helper")
        self.assertTrue(any("restart" in str(call) for call in calls))

    async def test_install_update_stages_and_schedules_apply_helper(self):
        plugin = self.module.Plugin()
        async def fake_check_update(force=False):
            return {
                "ok": True,
                "canInstall": True,
                "latest": "9.9.9",
                "hasUpdate": True,
                "elevated": True,
            }

        plugin.check_update = fake_check_update
        plugin._latest_release = lambda: {
            "tag_name": "v9.9.9",
            "assets": [{"name": "decky-renodx.zip", "browser_download_url": "https://example.invalid/decky-renodx.zip"}],
        }
        plugin._stage_release_zip = lambda _url: {
            "installedVersion": "9.9.9",
            "pluginPath": str(self.home / "plugin"),
            "stagingPath": str(self.home / "staging"),
            "backupPath": str(self.home / "backup"),
        }
        scheduled = []
        plugin._schedule_update_apply = lambda plugin_dir, staging_dir, backup_dir: (
            scheduled.append((plugin_dir, staging_dir, backup_dir)) or {"scheduled": True, "method": "helper"}
        )

        result = await plugin.install_update()

        self.assertTrue(result["ok"])
        self.assertTrue(result["requiresRestart"])
        self.assertTrue(result["restarted"])
        self.assertEqual(len(scheduled), 1)
        self.assertIn("staged", result["message"])

    async def test_install_release_zip_replaces_plugin_with_backup(self):
        plugin = self.module.Plugin()
        plugin_dir = self.home / "homebrew" / "plugins" / "decky-renodx"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "old.txt").write_text("old", encoding="utf-8")
        self.module.decky.DECKY_PLUGIN_DIR = str(plugin_dir)

        release_root = self.home / "release"
        release_plugin = release_root / "decky-renodx"
        (release_plugin / "dist").mkdir(parents=True)
        (release_plugin / "backend").mkdir(parents=True)
        (release_plugin / "plugin.json").write_text(json.dumps({"name": "Decky RenoDX"}), encoding="utf-8")
        (release_plugin / "package.json").write_text(json.dumps({"name": "decky-renodx", "version": "9.9.9"}), encoding="utf-8")
        (release_plugin / "dist" / "index.js").write_text("// ok", encoding="utf-8")
        (release_plugin / "main.py").write_text("# ok", encoding="utf-8")
        (release_plugin / "backend" / "__init__.py").write_text("# ok", encoding="utf-8")
        (release_plugin / "backend" / "cache.py").write_text("# ok", encoding="utf-8")
        archive_path = self.home / "release.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in release_plugin.rglob("*"):
                archive.write(path, path.relative_to(release_root))

        plugin._download_file = lambda _request, target: target.write_bytes(archive_path.read_bytes())

        result = plugin._install_release_zip("https://example.invalid/decky-renodx.zip")

        self.assertEqual(result["installedVersion"], "9.9.9")
        self.assertTrue(plugin_dir.exists())
        self.assertTrue((plugin_dir / "package.json").exists())
        self.assertTrue((plugin_dir.with_name("decky-renodx.previous") / "old.txt").exists())

    async def test_update_apply_helper_stops_copies_in_place_and_starts_loader(self):
        plugin = self.module.Plugin()
        plugin_dir = self.home / "plugins" / "decky-renodx"
        staging_dir = self.home / "plugins" / ".decky-renodx.update-test"
        backup_dir = self.home / "plugins" / "decky-renodx.previous"
        plugin_dir.mkdir(parents=True)
        staging_dir.mkdir(parents=True)
        commands = []

        original_run = self.module.subprocess.run

        def fake_run(argv, **kwargs):
            commands.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self.module.subprocess.run = fake_run
        try:
            result = plugin._schedule_update_apply(plugin_dir, staging_dir, backup_dir)
        finally:
            self.module.subprocess.run = original_run

        self.assertTrue(result["scheduled"])
        helper_path = Path(str(commands[0][2]).split("nohup ", 1)[1].split(" >/dev/null", 1)[0].strip("'\""))
        text = helper_path.read_text(encoding="utf-8")
        self.assertIn("stop plugin_loader", text)
        self.assertIn("mkdir -p \"$plugin_dir/dist\"", text)
        self.assertIn("cp -af \"$staging_dir/dist/.\" \"$plugin_dir/dist/\"", text)
        self.assertNotIn("mv \"$plugin_dir\" \"$backup_dir\"", text)
        self.assertNotIn("mv \"$staging_dir\" \"$plugin_dir\"", text)
        self.assertIn("start plugin_loader", text)


if __name__ == "__main__":
    unittest.main()
