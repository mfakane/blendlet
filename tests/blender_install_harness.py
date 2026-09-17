"""Checks the installed extension's enable/disable cycle. Run under Blender.

The add-on must already be installed into the Blender user resource path
used by this process (typically via BLENDER_USER_RESOURCES).
"""

from __future__ import annotations

import os
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

    print("BLENDLET_INSTALL_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
