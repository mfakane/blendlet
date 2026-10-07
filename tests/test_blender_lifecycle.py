from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO_ROOT / "src" / "blendlet"
HARNESS = REPO_ROOT / "tests" / "blender_harness.py"
UI_HARNESS = REPO_ROOT / "tests" / "blender_ui_harness.py"
EXAMPLES_HARNESS = REPO_ROOT / "tests" / "blender_examples_harness.py"
INSTALL_HARNESS = REPO_ROOT / "tests" / "blender_install_harness.py"


def find_blender() -> Path | None:
    for variable in ("BLENDER_EXECUTABLE", "BLENDER"):
        env = os.environ.get(variable)
        if env:
            candidate = Path(env)
            if candidate.exists():
                return candidate
    which = shutil.which("blender")
    if which:
        return Path(which)
    windows_default = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    if windows_default.exists():
        return windows_default
    return None


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


@unittest.skipUnless(find_blender() is not None, "Blender executable not found")
class BlenderHostLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        blender = find_blender()
        assert blender is not None
        cls.blender = blender

    def test_extension_manifest_validates(self):
        result = _run(
            [str(self.blender), "--command", "extension", "validate", str(ADDON_DIR)]
        )
        self.assertEqual(
            result.returncode,
            0,
            f"extension validate failed:\n{result.stdout}\n{result.stderr}",
        )

    def test_extension_builds_a_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            result = _run(
                [
                    str(self.blender),
                    "--command",
                    "extension",
                    "build",
                    "--source-dir",
                    str(ADDON_DIR),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            self.assertEqual(
                result.returncode,
                0,
                f"extension build failed:\n{result.stdout}\n{result.stderr}",
            )
            zips = list(output_dir.glob("*.zip"))
            self.assertEqual(len(zips), 1, f"expected one zip, got {zips}")
            self.assertGreater(zips[0].stat().st_size, 0)

    def test_enable_disable_without_blender_restart(self):
        result = _run(
            [
                str(self.blender),
                "--background",
                "--factory-startup",
                "--python",
                str(HARNESS),
            ]
        )
        output = f"{result.stdout}\n{result.stderr}"
        self.assertEqual(
            result.returncode,
            0,
            f"blender lifecycle harness failed:\n{output}",
        )
        self.assertIn("BLENDLET_LIFECYCLE_OK", result.stdout)

    def test_examples_load_and_work_in_background_blender(self):
        result = _run(
            [
                str(self.blender),
                "--background",
                "--factory-startup",
                "--python",
                str(EXAMPLES_HARNESS),
            ]
        )
        output = f"{result.stdout}\n{result.stderr}"
        self.assertEqual(
            result.returncode,
            0,
            f"blender examples harness failed:\n{output}",
        )
        self.assertIn("BLENDLET_EXAMPLES_OK", result.stdout)

    def test_status_panel_and_operators_in_background_blender(self):
        result = _run(
            [
                str(self.blender),
                "--background",
                "--factory-startup",
                "--python",
                str(UI_HARNESS),
            ]
        )
        output = f"{result.stdout}\n{result.stderr}"
        self.assertEqual(
            result.returncode,
            0,
            f"blender UI harness failed:\n{output}",
        )
        self.assertIn("BLENDLET_UI_OK", result.stdout)

    def test_installed_addon_enable_disable_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            custom_dir = tmp_path / "custom-blendlets"
            env = os.environ.copy()
            env["BLENDER_USER_RESOURCES"] = str(tmp_path)
            env["BLENDLET_TEST_DIRECTORY"] = str(custom_dir)

            build = _run(
                [
                    str(self.blender),
                    "--command",
                    "extension",
                    "build",
                    "--source-dir",
                    str(ADDON_DIR),
                    "--output-dir",
                    str(tmp_path),
                ],
                env=env,
            )
            self.assertEqual(
                build.returncode,
                0,
                f"extension build failed:\n{build.stdout}\n{build.stderr}",
            )
            zips = list(tmp_path.glob("blendlet-*.zip"))
            self.assertEqual(len(zips), 1, f"expected one zip, got {zips}")

            install = _run(
                [
                    str(self.blender),
                    "--command",
                    "extension",
                    "install-file",
                    "-r",
                    "user_default",
                    "-e",
                    str(zips[0]),
                ],
                env=env,
            )
            self.assertEqual(
                install.returncode,
                0,
                f"extension install-file failed:\n{install.stdout}\n{install.stderr}",
            )
            self.assertIn('Installed "blendlet"', install.stdout)

            result = _run(
                [
                    str(self.blender),
                    "--background",
                    "--python",
                    str(INSTALL_HARNESS),
                ],
                env=env,
            )
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(
                result.returncode,
                0,
                f"installed add-on lifecycle failed:\n{output}",
            )
            self.assertIn("BLENDLET_INSTALL_OK", result.stdout)


if __name__ == "__main__":
    sys.exit(unittest.main())
