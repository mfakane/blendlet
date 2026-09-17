"""In-process Blendlet lifecycle checks. Run under Blender, not CPython.

blender --background --factory-startup --python tests/blender_harness.py
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _fail(message: str) -> None:
    print(f"BLENDLET_LIFECYCLE_FAIL: {message}", flush=True)
    raise SystemExit(1)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def main() -> None:
    import blendlet
    from blendlet.host import get_host, reset_host
    from blendlet.paths import resolve_blendlets_directory
    from blendlet.preferences import BlendletPreferences

    reset_host()

    blendlet.register()
    host = get_host()
    _assert(host.running, "host should be running after register()")
    _assert(
        host.blendlets_directory == resolve_blendlets_directory(""),
        f"host directory {host.blendlets_directory!s} should be the default",
    )
    _assert(BlendletPreferences.is_registered, "preferences class should be registered")

    blendlet.unregister()
    _assert(not host.running, "host should stop on unregister()")
    _assert(
        host.blendlets_directory is None,
        "host should drop the directory on unregister()",
    )
    _assert(
        not BlendletPreferences.is_registered,
        "preferences class should be unregistered on disable",
    )

    blendlet.register()
    _assert(host.running, "host should start again without restarting Blender")
    _assert(
        BlendletPreferences.is_registered,
        "preferences class should register again without restarting Blender",
    )
    custom = Path(tempfile.mkdtemp()).resolve()
    host.set_blendlets_directory(custom)
    _assert(
        host.blendlets_directory == custom,
        "running host should accept a newly configured directory",
    )

    blendlet.unregister()
    _assert(not host.running, "second unregister() should stop the host")
    _assert(not BlendletPreferences.is_registered, "preferences should unregister on disable")
    blendlet.unregister()
    _assert(not host.running, "extra unregister() must be safe")
    _assert(not BlendletPreferences.is_registered, "extra unregister() must leave prefs unregistered")

    print("BLENDLET_LIFECYCLE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
