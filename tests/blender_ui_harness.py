"""Headless Blendlet status-panel checks. Run under Blender, not CPython.

blender --background --factory-startup --python tests/blender_ui_harness.py

Background mode has no 3D View region to click, so operators are invoked through
``bpy.ops`` and ``Panel.draw`` is exercised against a recording layout. The OS
clipboard is inert under ``--background``; copy-error content is checked by
calling ``execute`` with a recording ``window_manager``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

MARKER = """\
def register(ctx):
    import bpy
    bpy.app.driver_namespace["BLENDLET_UI_MARKER"] = ctx.name

def unregister(ctx):
    import bpy
    bpy.app.driver_namespace.pop("BLENDLET_UI_MARKER", None)
"""

BROKEN_REGISTER = """\
def register(ctx):
    raise RuntimeError("panel-boom")
"""


def _fail(message: str) -> None:
    print(f"BLENDLET_UI_FAIL: {message}", flush=True)
    raise SystemExit(1)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _status_entry(status_path: Path, name: str) -> dict:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    matches = [item for item in payload["blendlets"] if item["name"] == name]
    _assert(len(matches) == 1, f"expected one {name!r} status entry, got {payload}")
    return matches[0]


class RecordingLayout:
    """Stand-in for ``UILayout`` so ``Panel.draw`` can run without a region."""

    def __init__(self, log: dict) -> None:
        self._log = log
        self.enabled = True
        self.alert = False

    def row(self, align: bool = False) -> RecordingLayout:
        return RecordingLayout(self._log)

    def box(self) -> RecordingLayout:
        return RecordingLayout(self._log)

    def label(self, text: str = "", icon: str = "NONE") -> None:
        self._log["labels"].append(text)

    def operator(self, idname: str, text: str = "", icon: str = "NONE") -> SimpleNamespace:
        props = SimpleNamespace(path="", enabled=True)
        self._log["operators"].append({"id": idname, "text": text, "props": props})
        return props


def _draw_panel(panel_cls) -> dict:
    log: dict = {"labels": [], "operators": []}

    class DrawHost:
        """Plain host for Panel.draw; RNA Panel instances cannot be constructed."""

        draw = panel_cls.draw
        _draw_blendlet = panel_cls._draw_blendlet

        def __init__(self) -> None:
            self.layout = RecordingLayout(log)

    DrawHost().draw(None)
    return log


def _ops_of(log: dict, idname: str) -> list[dict]:
    return [item for item in log["operators"] if item["id"] == idname]


def main() -> None:
    import bpy

    _assert(bool(bpy.app.background), "UI harness must run in Blender background mode")

    import blendlet
    from blendlet.host import get_host, reset_host
    from blendlet.ui import (
        BLENDLET_OT_copy_error,
        BLENDLET_OT_open_directory,
        BLENDLET_OT_reload,
        BLENDLET_OT_reload_all,
        BLENDLET_OT_set_enabled,
        BLENDLET_PT_status,
    )

    reset_host()
    blendlet.register()
    host = get_host()
    _assert(host.running, "host should start with the add-on")
    _assert(BLENDLET_PT_status.is_registered, "status panel should be registered")
    _assert(BLENDLET_OT_reload.is_registered, "reload operator should be registered")
    _assert(BLENDLET_OT_reload_all.is_registered, "reload-all operator should be registered")
    _assert(BLENDLET_OT_set_enabled.is_registered, "enable operator should be registered")
    _assert(BLENDLET_OT_copy_error.is_registered, "copy-error operator should be registered")
    _assert(BLENDLET_OT_open_directory.is_registered, "open-directory operator should be registered")

    directory = Path(tempfile.mkdtemp()).resolve()
    host.set_blendlets_directory(directory)
    status_path = directory / ".blendlet" / "status.json"

    empty = _draw_panel(BLENDLET_PT_status)
    _assert(str(directory) in empty["labels"], f"empty panel should show the directory, got {empty['labels']}")
    _assert("No blendlets in this directory" in empty["labels"], f"empty panel should say so, got {empty['labels']}")
    _assert(
        _ops_of(empty, "blendlet.reload_all"),
        f"empty panel should still offer Reload All, got {empty['operators']}",
    )
    _assert(
        BLENDLET_OT_open_directory.poll(bpy.context),
        "Open Directory should be available once the folder exists",
    )

    marker = directory / "marker.py"
    marker.write_text(MARKER, encoding="utf-8")
    reload_result = bpy.ops.blendlet.reload(path=str(marker))
    _assert("FINISHED" in reload_result, f"Reload of a new file failed: {reload_result}")
    _assert(
        bpy.app.driver_namespace.get("BLENDLET_UI_MARKER") == "marker",
        "Reload operator should register the blendlet inside this Blender process",
    )
    _assert(_status_entry(status_path, "marker")["state"] == "loaded", "status.json should report loaded")

    loaded = _draw_panel(BLENDLET_PT_status)
    _assert("marker" in loaded["labels"], f"panel should list the blendlet name, got {loaded['labels']}")
    _assert("Loaded" in loaded["labels"], f"panel should show Loaded, got {loaded['labels']}")
    reload_buttons = _ops_of(loaded, "blendlet.reload")
    _assert(len(reload_buttons) == 1, f"expected one Reload button, got {loaded['operators']}")
    _assert(reload_buttons[0]["props"].path == str(marker), f"Reload should target {marker}")
    disable_buttons = [item for item in _ops_of(loaded, "blendlet.set_enabled") if item["text"] == "Disable"]
    _assert(len(disable_buttons) == 1, f"loaded row should offer Disable, got {loaded['operators']}")
    _assert(disable_buttons[0]["props"].enabled is False, "Disable button should pass enabled=False")

    disable_result = bpy.ops.blendlet.set_enabled(path=str(marker), enabled=False)
    _assert("FINISHED" in disable_result, f"Disable operator failed: {disable_result}")
    _assert(
        "BLENDLET_UI_MARKER" not in bpy.app.driver_namespace,
        "Disable operator should unregister the running tool",
    )
    _assert(_status_entry(status_path, "marker")["state"] == "disabled", "status.json should report disabled")

    disabled = _draw_panel(BLENDLET_PT_status)
    _assert("Disabled" in disabled["labels"], f"panel should show Disabled, got {disabled['labels']}")
    enable_buttons = [item for item in _ops_of(disabled, "blendlet.set_enabled") if item["text"] == "Enable"]
    _assert(len(enable_buttons) == 1, f"disabled row should offer Enable, got {disabled['operators']}")
    _assert(enable_buttons[0]["props"].enabled is True, "Enable button should pass enabled=True")

    enable_result = bpy.ops.blendlet.set_enabled(path=str(marker), enabled=True)
    _assert("FINISHED" in enable_result, f"Enable operator failed: {enable_result}")
    _assert(
        bpy.app.driver_namespace.get("BLENDLET_UI_MARKER") == "marker",
        "Enable operator should load the tool again",
    )

    marker.write_text(BROKEN_REGISTER, encoding="utf-8")
    failed_reload = bpy.ops.blendlet.reload(path=str(marker))
    _assert("FINISHED" in failed_reload, f"Reload of a broken file failed: {failed_reload}")
    _assert(
        bpy.app.driver_namespace.get("BLENDLET_UI_MARKER") == "marker",
        "failed reload should keep last-good registered",
    )
    failed_status = _status_entry(status_path, "marker")
    _assert(failed_status["state"] == "error", f"failed reload should be error: {failed_status}")
    _assert(failed_status["last_good"] is True, f"failed reload should keep last-good: {failed_status}")
    _assert(
        failed_status["error"] is not None and failed_status["error"]["type"] == "RuntimeError",
        f"status should expose RuntimeError, got {failed_status}",
    )

    failed = _draw_panel(BLENDLET_PT_status)
    _assert(
        "Failed — last good active" in failed["labels"],
        f"panel should show last-good failure, got {failed['labels']}",
    )
    _assert(
        any("panel-boom" in label for label in failed["labels"]),
        f"panel should show the traceback without a terminal, got {failed['labels']}",
    )
    copy_buttons = _ops_of(failed, "blendlet.copy_error")
    _assert(len(copy_buttons) == 1, f"failed row should offer Copy Error, got {failed['operators']}")
    _assert(copy_buttons[0]["props"].path == str(marker), "Copy Error should target the failed blendlet")

    copy_result = bpy.ops.blendlet.copy_error(path=str(marker))
    _assert("FINISHED" in copy_result, f"Copy Error operator failed: {copy_result}")
    recorded = SimpleNamespace(clipboard="")
    op = SimpleNamespace(path=str(marker), report=lambda _kind, _msg: None)
    copied = BLENDLET_OT_copy_error.execute(
        op, SimpleNamespace(window_manager=recorded)
    )
    _assert(copied == {"FINISHED"}, f"copy execute should finish, got {copied}")
    _assert(
        "RuntimeError" in recorded.clipboard,
        f"copied text should contain the error type, got {recorded.clipboard!r}",
    )
    _assert(
        "panel-boom" in recorded.clipboard,
        f"copied text should contain the traceback, got {recorded.clipboard!r}",
    )

    other = directory / "other.py"
    other.write_text(MARKER.replace("BLENDLET_UI_MARKER", "BLENDLET_UI_OTHER"), encoding="utf-8")
    _assert("FINISHED" in bpy.ops.blendlet.reload(path=str(other)), "Reload of second blendlet failed")
    _assert("FINISHED" in bpy.ops.blendlet.set_enabled(path=str(other), enabled=False), "Disable of second failed")
    _assert("BLENDLET_UI_OTHER" not in bpy.app.driver_namespace, "disabled second tool should be unloaded")
    _assert("FINISHED" in bpy.ops.blendlet.reload_all(), "Reload All failed")
    _assert(
        bpy.app.driver_namespace.get("BLENDLET_UI_OTHER") == "other",
        "Reload All should re-enable and load current files",
    )
    _assert(_status_entry(status_path, "other")["state"] == "loaded", "Reload All should leave other loaded")

    blendlet.unregister()
    _assert(not BLENDLET_PT_status.is_registered, "disable should unregister the panel")
    _assert(not BLENDLET_OT_reload.is_registered, "disable should unregister operators")
    _assert("BLENDLET_UI_MARKER" not in bpy.app.driver_namespace, "disable should unload last-good")

    print("BLENDLET_UI_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
