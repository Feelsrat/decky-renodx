import importlib.util
import json
import sys
import tempfile
import types
import unittest
import zipfile
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


if __name__ == "__main__":
    unittest.main()
