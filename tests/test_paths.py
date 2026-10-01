from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blendlet.paths import (
    default_blendlets_directory,
    project_blendlets_directory,
    resolve_blendlets_directory,
)


class ProjectBlendletsDirectoryTests(unittest.TestCase):
    def test_unsaved_file_has_no_project_directory(self):
        self.assertIsNone(project_blendlets_directory(""))

    def test_project_directory_is_beside_the_blend_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blend = Path(tmp) / "scene.blend"
            self.assertEqual(
                project_blendlets_directory(str(blend)),
                (Path(tmp) / ".blendlet").resolve(),
            )


class ResolveBlendletsDirectoryTests(unittest.TestCase):
    def test_empty_preference_uses_home_blendlets(self):
        expected = (Path.home() / "blendlets").resolve()
        self.assertEqual(resolve_blendlets_directory(""), expected)
        self.assertEqual(resolve_blendlets_directory("   "), expected)
        self.assertEqual(resolve_blendlets_directory(None), expected)
        self.assertEqual(default_blendlets_directory().resolve(), expected)

    def test_injected_home_changes_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            resolved = resolve_blendlets_directory("", home=home)
            self.assertEqual(resolved, (home / "blendlets").resolve())

    def test_tilde_expands_to_home(self):
        resolved = resolve_blendlets_directory("~/blendlets-custom")
        self.assertEqual(resolved, (Path.home() / "blendlets-custom").resolve())

    def test_relative_path_resolves_against_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            resolved = resolve_blendlets_directory("tools", cwd=cwd)
            self.assertEqual(resolved, (cwd / "tools").resolve())

    def test_absolute_path_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = Path(tmp) / "blendlets"
            resolved = resolve_blendlets_directory(str(configured))
            self.assertEqual(resolved, configured.resolve())
            self.assertTrue(resolved.is_absolute())

    def test_missing_directory_still_resolves(self):
        missing = Path(tempfile.gettempdir()) / "blendlet-missing-dir-does-not-exist"
        resolved = resolve_blendlets_directory(str(missing))
        self.assertEqual(resolved, missing.resolve())
        self.assertFalse(resolved.exists())
