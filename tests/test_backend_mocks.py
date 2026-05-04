import importlib.util
import json
import sys
import tempfile
import types
import unittest
import zipfile
import tarfile
from pathlib import Path


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
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "Decky RenoDX"}), encoding="utf-8")
        (plugin_dir / "package.json").write_text(json.dumps({"name": "decky-renodx", "version": "0.1.0"}), encoding="utf-8")
        (plugin_dir / "dist" / "index.js").write_text("// ok", encoding="utf-8")
        (plugin_dir / "main.py").write_text("# ok", encoding="utf-8")

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
        self.assertEqual(result["games"], [{"appid": "123", "name": "Example Game"}])

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
        with tarfile.open(bin_dir / "autohdr_addon.tar.gz", "w:gz") as archive:
            archive.add(source / "AutoHDR32.addon", arcname="AutoHDR32.addon")
            archive.add(source / "AutoHDR64.addon", arcname="AutoHDR64.addon")
        with tarfile.open(bin_dir / "advanced_autohdr_effect.tar.gz", "w:gz") as archive:
            archive.add(source / "AutoHDR.fx", arcname="Shaders/AutoHDR.fx")

        plugin._install_autohdr_payloads(main_path, bin_dir)

        for name in ["AutoHDR32.addon", "AutoHDR64.addon", "AutoHDR.addon32", "AutoHDR.addon64"]:
            self.assertTrue((main_path / "AutoHDR_addons" / name).exists())

    async def test_restart_uses_helper_when_systemd_run_fails(self):
        plugin = self.module.Plugin()
        calls = []

        class Result:
            def __init__(self, returncode):
                self.returncode = returncode
                self.stderr = "failed"

        original_run = self.module.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ["bash", "-lc"]:
                return Result(0)
            return Result(1)

        self.module.subprocess.run = fake_run
        try:
            result = plugin._schedule_loader_restart("test")
        finally:
            self.module.subprocess.run = original_run

        self.assertTrue(result["scheduled"])
        self.assertEqual(result["method"], "helper")
        self.assertTrue(any(call[:2] == ["bash", "-lc"] for call in calls))


if __name__ == "__main__":
    unittest.main()
