"""In-process Blendlet lifecycle checks. Run under Blender, not CPython.

blender --background --factory-startup --python tests/blender_harness.py
"""

from __future__ import annotations

import sys
import tempfile
import time
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


def _wait_for_events(host, collected, count: int, message: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host.poll()
        if len(collected) >= count:
            return
        time.sleep(0.05)
    _fail(f"{message}: timed out waiting for {count} events, got {collected}")


def _assert_no_further_events(host, collected, label: str) -> None:
    time.sleep(0.5)
    host.poll()
    _assert(len(collected) == 1, f"{label} produced extra events: {collected}")


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
    _assert(host.is_timer_registered(), "discovery timer should be registered while the host runs")
    _assert(host.watching, "host should be watching the blendlets directory")

    events = []
    host.subscribe(events.append)

    tool = custom / "tool.py"
    tool.write_text("value = 1\n", encoding="utf-8")
    _wait_for_events(host, events, 1, "creating a blendlet should emit one event")
    _assert(events[0].kind.value == "created", f"expected created, got {events}")
    _assert(events[0].path == tool, f"created path should be {tool}, got {events}")
    _assert(host.discovered_files() == (tool,), f"discovered {host.discovered_files()}")
    _assert_no_further_events(host, events, "create")

    events.clear()
    tool.write_text("value = 2\n", encoding="utf-8")
    tool.write_text("value = 3\n", encoding="utf-8")
    tmp = custom / "tool.py.tmp"
    tmp.write_text("value = 4\n", encoding="utf-8")
    tmp.replace(tool)
    _wait_for_events(host, events, 1, "rapid replacement should emit one modified event")
    _assert(len(events) == 1, f"rapid replacement caused a reload storm: {events}")
    _assert(events[0].kind.value == "modified", f"expected modified, got {events}")
    _assert_no_further_events(host, events, "rapid replacement")

    events.clear()
    tool.unlink()
    _wait_for_events(host, events, 1, "deleting a blendlet should emit one event")
    _assert(events[0].kind.value == "deleted", f"expected deleted, got {events}")
    _assert(host.discovered_files() == (), f"deleted file still discovered: {host.discovered_files()}")
    _assert_no_further_events(host, events, "delete")

    blendlet.unregister()
    _assert(not host.running, "second unregister() should stop the host")
    _assert(not host.watching, "unregister() should stop watching")
    _assert(not host.is_timer_registered(), "unregister() should drop the discovery timer")
    _assert(not BlendletPreferences.is_registered, "preferences should unregister on disable")

    events.clear()
    leftover = custom / "after-stop.py"
    leftover.write_text("too_late = True\n", encoding="utf-8")
    time.sleep(0.5)
    host.poll()
    _assert(events == [], f"disabled host should not emit file events, got {events}")

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
