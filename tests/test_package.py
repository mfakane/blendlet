from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "src" / "blendlet"


class PackageMetadataTests(unittest.TestCase):
    def test_manifest_declares_an_installable_addon(self):
        manifest_path = ADDON_DIR / "blender_manifest.toml"
        self.assertTrue(manifest_path.is_file(), "blender_manifest.toml is required")
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["type"], "add-on")
        self.assertEqual(manifest["id"], "blendlet")
        self.assertEqual(manifest["schema_version"], "1.0.0")
        self.assertEqual(manifest["blender_version_min"], "4.2.0")
        self.assertIn("SPDX:GPL-3.0-or-later", manifest["license"])
        self.assertTrue((ADDON_DIR / "__init__.py").is_file())
        init_text = (ADDON_DIR / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("def register()", init_text)
        self.assertIn("def unregister()", init_text)
