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

    import bpy

    events.clear()
    persistent = custom / "marker.py"
    persistent.write_text(
        "def register(ctx):\n"
        "    import bpy\n"
        "    bpy.app.driver_namespace['BLENDLET_MARKER'] = ctx.name\n"
        "\n"
        "def unregister(ctx):\n"
        "    import bpy\n"
        "    bpy.app.driver_namespace.pop('BLENDLET_MARKER', None)\n",
        encoding="utf-8",
    )
    _wait_for_events(host, events, 1, "creating a persistent blendlet should emit one event")
    _assert(
        bpy.app.driver_namespace.get("BLENDLET_MARKER") == "marker",
        "register(ctx) should run with ordinary bpy available",
    )
    _assert(
        host.persistent_files() == (persistent,),
        f"persistent files should be {(persistent,)}, got {host.persistent_files()}",
    )

    events.clear()
    once = custom / "once.py"
    once.write_text(
        "from pathlib import Path\n"
        "def run(ctx):\n"
        "    Path(__file__).with_suffix('.out').write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    _wait_for_events(host, events, 1, "creating a transient blendlet should emit one event")
    out = once.with_suffix(".out")
    _assert(out.is_file() and out.read_text(encoding="utf-8") == "ran", "run(ctx) should execute")
    _assert(
        once not in host.persistent_files(),
        f"run() script became persistent: {host.persistent_files()}",
    )

    events.clear()
    persistent.unlink()
    _wait_for_events(host, events, 1, "deleting a persistent blendlet should emit one event")
    _assert(
        "BLENDLET_MARKER" not in bpy.app.driver_namespace,
        "unregister(ctx) should run when a persistent blendlet is deleted",
    )
    _assert(host.persistent_files() == (), f"deleted persistent still loaded: {host.persistent_files()}")

    owned_source = (
        "import bpy\n"
        "from bpy.types import Operator\n"
        "\n"
        "class BLENDLET_OT_owned(Operator):\n"
        "    bl_idname = 'blendlet.owned_probe'\n"
        "    bl_label = 'Owned Probe'\n"
        "    def execute(self, context):\n"
        "        return {'FINISHED'}\n"
        "\n"
        "def tick():\n"
        "    return 1.0\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_class(BLENDLET_OT_owned)\n"
        "    ctx.add_timer(tick, persistent=True)\n"
        "    bpy.app.driver_namespace['BLENDLET_OWNED_TIMER'] = tick\n"
    )
    events.clear()
    owned = custom / "owned.py"
    owned.write_text(owned_source, encoding="utf-8")
    _wait_for_events(host, events, 1, "creating an owned-resource blendlet should emit one event")
    owned_record = host.loaded_blendlet(owned)
    _assert(owned_record is not None, "owned blendlet should be loaded")
    _assert(
        owned_record.error is None,
        f"owned blendlet failed: {owned_record.error!r}",
    )
    _assert(owned_record.persistent, "owned blendlet should be persistent")
    _assert(
        owned_record.module.BLENDLET_OT_owned.is_registered,
        "ctx.register_class should register the operator",
    )
    first_cls = owned_record.module.BLENDLET_OT_owned
    owned_tick = bpy.app.driver_namespace.get("BLENDLET_OWNED_TIMER")
    _assert(owned_tick is not None, "owned timer function should be recorded")
    _assert(bpy.app.timers.is_registered(owned_tick), "ctx.add_timer should register a timer")

    events.clear()
    owned.write_text(owned_source + "\nMARKER = 1\n", encoding="utf-8")
    _wait_for_events(host, events, 1, "modifying an owned-resource blendlet should emit one event")
    owned_record = host.loaded_blendlet(owned)
    _assert(owned_record is not None, "reloaded owned blendlet should be loaded")
    _assert(
        owned_record.error is None,
        f"reload of owned blendlet failed: {owned_record.error!r}",
    )
    _assert(
        owned_record.module.BLENDLET_OT_owned.is_registered,
        "reloaded operator should stay registered once",
    )
    _assert(not first_cls.is_registered, "reload should unregister the previous operator class")
    owned_cls = owned_record.module.BLENDLET_OT_owned
    new_tick = bpy.app.driver_namespace.get("BLENDLET_OWNED_TIMER")
    _assert(new_tick is not owned_tick, "reload should register a new timer function")
    _assert(
        not bpy.app.timers.is_registered(owned_tick),
        "reload should unregister the previous timer",
    )
    _assert(bpy.app.timers.is_registered(new_tick), "reload should register the new timer")

    events.clear()
    owned.unlink()
    _wait_for_events(host, events, 1, "deleting an owned-resource blendlet should emit one event")
    _assert(host.loaded_blendlet(owned) is None, "deleted owned blendlet should unload")
    _assert(
        not owned_cls.is_registered,
        "delete should unregister classes owned by ctx",
    )
    _assert(
        not bpy.app.timers.is_registered(new_tick),
        "delete should unregister timers owned by ctx",
    )

    events.clear()
    disable_owned = custom / "disable_owned.py"
    disable_owned.write_text(owned_source, encoding="utf-8")
    _wait_for_events(host, events, 1, "creating a blendlet before disable should emit one event")
    disable_record = host.loaded_blendlet(disable_owned)
    _assert(disable_record is not None and disable_record.error is None, "disable blendlet should load")
    disable_cls = disable_record.module.BLENDLET_OT_owned
    disable_tick = bpy.app.driver_namespace.get("BLENDLET_OWNED_TIMER")
    _assert(
        disable_cls.is_registered,
        "operator should be registered before disable",
    )
    _assert(
        disable_tick is not None and bpy.app.timers.is_registered(disable_tick),
        "timer should be registered before disable",
    )

    blendlet.unregister()
    _assert(not host.running, "second unregister() should stop the host")
    _assert(not host.watching, "unregister() should stop watching")
    _assert(not host.is_timer_registered(), "unregister() should drop the discovery timer")
    _assert(not BlendletPreferences.is_registered, "preferences should unregister on disable")
    _assert(
        not disable_cls.is_registered,
        "add-on disable should dispose classes owned by ctx",
    )
    _assert(
        not bpy.app.timers.is_registered(disable_tick),
        "add-on disable should dispose timers owned by ctx",
    )

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
