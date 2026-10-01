"""Checks the installed extension's enable/disable cycle. Run under Blender.

The add-on must already be installed into the Blender user resource path
used by this process (typically via BLENDER_USER_RESOURCES).
"""

from __future__ import annotations

import os
import tempfile
import time
import traceback
from pathlib import Path


def _fail(message: str) -> None:
    print(f"BLENDLET_INSTALL_FAIL: {message}", flush=True)
    raise SystemExit(1)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def main() -> None:
    import addon_utils
    import bpy

    import bl_ext.user_default.blendlet as blendlet
    from bl_ext.user_default.blendlet.host import get_host
    from bl_ext.user_default.blendlet.paths import resolve_blendlets_directory
    from bl_ext.user_default.blendlet.preferences import BlendletPreferences
    from bl_ext.user_default.blendlet.ui import BLENDLET_OT_reload, BLENDLET_PT_status

    package = blendlet.__package__
    _assert(package == "bl_ext.user_default.blendlet", f"unexpected package {package}")
    _assert(package in bpy.context.preferences.addons, "installed add-on should have preferences")

    host = get_host()
    _assert(host.running, "installed add-on should start the host")
    _assert(host.watching, "installed add-on should watch the blendlets directory")
    _assert(host.is_timer_registered(), "installed add-on should register a discovery timer")
    _assert(
        host.blendlets_directory == resolve_blendlets_directory(""),
        f"empty preference should resolve to default, got {host.blendlets_directory}",
    )

    custom = Path(os.environ["BLENDLET_TEST_DIRECTORY"]).resolve()
    prefs = bpy.context.preferences.addons[package].preferences
    prefs.blendlets_directory = str(custom)
    _assert(
        host.blendlets_directory == custom,
        f"preference update should re-resolve, got {host.blendlets_directory}",
    )

    addon_utils.disable(package)
    _assert(not host.running, "disable should stop the host")
    _assert(not host.watching, "disable should stop watching")
    _assert(not host.is_timer_registered(), "disable should unregister the discovery timer")
    _assert(host.blendlets_directory is None, "disable should clear the host directory")
    _assert(not BlendletPreferences.is_registered, "disable should unregister preferences")
    _assert(not BLENDLET_PT_status.is_registered, "disable should unregister the status panel")
    _assert(not BLENDLET_OT_reload.is_registered, "disable should unregister host operators")

    addon_utils.enable(package)
    host = get_host()
    _assert(host.running, "enable should start the host without restarting Blender")
    _assert(host.watching, "enable should resume watching without restarting Blender")
    _assert(host.is_timer_registered(), "enable should register the discovery timer again")
    _assert(
        host.blendlets_directory == custom,
        f"re-enable should keep the configured directory, got {host.blendlets_directory}",
    )
    _assert(BlendletPreferences.is_registered, "enable should register preferences")
    _assert(BLENDLET_PT_status.is_registered, "enable should register the status panel")
    _assert(BLENDLET_OT_reload.is_registered, "enable should register host operators")

    _check_remembered_trust(package)

    print("BLENDLET_INSTALL_OK", flush=True)


LOGGING = """\
from pathlib import Path
LOG = Path(__file__).with_suffix(".log")

def _append(line):
    previous = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    LOG.write_text(previous + line + "\\n", encoding="utf-8")

def register(ctx):
    _append("register")

def unregister(ctx):
    _append("unregister")
"""


def _log_lines(path: Path) -> list[str]:
    log = path.with_suffix(".log")
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


def _wait_until(host, predicate, message: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host.poll()
        if predicate():
            return
        time.sleep(0.05)
    _fail(f"{message}: timed out")


def _check_remembered_trust(package: str) -> None:
    """Trust and Remember survives an add-on restart through preferences."""
    import addon_utils
    import bpy

    from bl_ext.user_default.blendlet import project
    from bl_ext.user_default.blendlet.host import get_host

    host = get_host()
    root = Path(tempfile.mkdtemp()).resolve()
    project_dir = root / ".blendlet"
    project_dir.mkdir()
    tool = project_dir / "remembered.py"
    tool.write_text(LOGGING, encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(root / "scene.blend"))
    _assert(host.project_directory is None, "an unknown folder should not be trusted")

    prefs = bpy.context.preferences
    prefs.is_dirty = False
    result = bpy.ops.blendlet.trust_project(remember=True)
    _assert("FINISHED" in result, f"Trust and Remember failed: {result}")
    remembered = [item.path for item in prefs.addons[package].preferences.trusted_directories]
    _assert(remembered == [str(project_dir)], f"trusted folder should be remembered, got {remembered}")
    _assert(prefs.is_dirty, "remembering trust should mark preferences for saving")
    _wait_until(host, lambda: _log_lines(tool) == ["register"], "remembered folder should load")

    addon_utils.disable(package)
    _assert(_log_lines(tool) == ["register", "unregister"], f"disable should unload, got {_log_lines(tool)}")
    addon_utils.enable(package)
    host = get_host()
    _assert(
        host.project_directory is None,
        "register() cannot read bpy.data under addon_utils, so trust is applied afterwards",
    )
    _assert(bpy.app.timers.is_registered(project._sync_once), "enable should schedule a project sync")
    project._sync_once()
    _assert(host.project_directory == project_dir, "remembered trust should survive an add-on restart")
    _wait_until(
        host,
        lambda: _log_lines(tool) == ["register", "unregister", "register"],
        "remembered folder should load again after enable",
    )

    result = bpy.ops.blendlet.forget_trusted_directory(path=str(project_dir))
    _assert("FINISHED" in result, f"forget failed: {result}")
    _assert(
        len(prefs.addons[package].preferences.trusted_directories) == 0,
        "forget should remove the remembered folder",
    )
    _assert(host.project_directory is None, "forget should stop watching the folder")
    _assert(
        _log_lines(tool) == ["register", "unregister", "register", "unregister"],
        f"forget should unload the folder's blendlets, got {_log_lines(tool)}",
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
