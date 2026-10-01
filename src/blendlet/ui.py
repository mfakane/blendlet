"""3D View sidebar for blendlet status and host controls."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator, Panel

from . import project
from .discovery import iter_blendlet_files
from .host import get_host
from .status import display_state, error_preview_lines, latest_edit_loaded


def _state_icon(entry: dict) -> str:
    state = entry.get("state")
    if state in {"loaded", "ran"}:
        return "CHECKMARK"
    if state == "error":
        return "ERROR"
    if state == "reloading":
        return "FILE_REFRESH"
    if state == "disabled":
        return "PAUSE"
    return "DOT"


def _entry_for(path: str) -> dict | None:
    host = get_host()
    for snapshot in (host.status_snapshot(), host.project_status_snapshot()):
        if snapshot is None:
            continue
        for entry in snapshot["blendlets"]:
            if entry["path"] == path:
                return entry
    return None


class BLENDLET_OT_reload(Operator):
    bl_idname = "blendlet.reload"
    bl_label = "Reload Blendlet"
    bl_description = "Load this blendlet from disk now"
    bl_options = {"REGISTER", "INTERNAL"}

    path: StringProperty(name="Path", options={"HIDDEN"})

    @classmethod
    def poll(cls, _context) -> bool:
        return get_host().running

    def execute(self, _context):
        try:
            get_host().reload_now(Path(self.path))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDLET_OT_reload_all(Operator):
    bl_idname = "blendlet.reload_all"
    bl_label = "Reload All"
    bl_description = "Load every discovered blendlet from disk now"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, _context) -> bool:
        return get_host().running

    def execute(self, _context):
        try:
            get_host().reload_all()
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDLET_OT_set_enabled(Operator):
    bl_idname = "blendlet.set_enabled"
    bl_label = "Enable Blendlet"
    bl_description = "Enable or disable this blendlet without deleting the file"
    bl_options = {"REGISTER", "INTERNAL"}

    path: StringProperty(name="Path", options={"HIDDEN"})
    enabled: BoolProperty(name="Enabled", default=True)

    @classmethod
    def poll(cls, _context) -> bool:
        return get_host().running

    def execute(self, _context):
        host = get_host()
        path = Path(self.path)
        try:
            if self.enabled:
                host.enable(path)
            else:
                host.disable(path)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDLET_OT_open_directory(Operator):
    bl_idname = "blendlet.open_directory"
    bl_label = "Open Blendlets Directory"
    bl_description = "Open the configured blendlets directory in the file browser"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, _context) -> bool:
        directory = get_host().blendlets_directory
        return directory is not None and directory.is_dir()

    def execute(self, _context):
        directory = get_host().blendlets_directory
        if directory is None:
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=str(directory))
        return {"FINISHED"}


class BLENDLET_OT_copy_error(Operator):
    bl_idname = "blendlet.copy_error"
    bl_label = "Copy Error"
    bl_description = "Copy the latest reload traceback to the clipboard"
    bl_options = {"REGISTER", "INTERNAL"}

    path: StringProperty(name="Path", options={"HIDDEN"})

    def execute(self, context):
        entry = _entry_for(self.path)
        error = None if entry is None else entry.get("error")
        if not error:
            self.report({"WARNING"}, "No reload error to copy")
            return {"CANCELLED"}
        text = (error.get("traceback") or "").rstrip()
        if not text:
            type_name = error.get("type") or "Error"
            message = error.get("message") or ""
            text = f"{type_name}: {message}".rstrip() if message else type_name
        context.window_manager.clipboard = text + "\n"
        self.report({"INFO"}, "Copied traceback")
        return {"FINISHED"}


class BLENDLET_OT_trust_project(Operator):
    bl_idname = "blendlet.trust_project"
    bl_label = "Trust Project Blendlets"
    bl_options = {"REGISTER", "INTERNAL"}

    remember: BoolProperty(name="Remember", default=False)

    @classmethod
    def description(cls, _context, properties) -> str:
        if properties.remember:
            return (
                "Run blendlets from the .blendlet folder beside this .blend file, "
                "now and in later sessions"
            )
        return "Run blendlets from the .blendlet folder beside this .blend file for this session"

    @classmethod
    def poll(cls, _context) -> bool:
        return get_host().running and project.current_directory() is not None

    def execute(self, _context):
        directory = project.current_directory()
        if directory is None:
            return {"CANCELLED"}
        if not project.trust(directory, remember=self.remember):
            self.report({"ERROR"}, "Add-on preferences are unavailable; trust was not remembered")
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDLET_OT_forget_trusted_directory(Operator):
    bl_idname = "blendlet.forget_trusted_directory"
    bl_label = "Forget Trusted Folder"
    bl_description = "Stop trusting this folder. Its blendlets unload if they are running"
    bl_options = {"REGISTER", "INTERNAL"}

    path: StringProperty(name="Path", options={"HIDDEN"})

    def execute(self, _context):
        project.forget(Path(self.path))
        return {"FINISHED"}


class BLENDLET_PT_status(Panel):
    bl_label = "Blendlet"
    bl_idname = "BLENDLET_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendlet"

    def draw(self, _context) -> None:
        layout = self.layout
        host = get_host()
        if not host.running:
            layout.label(text="Host is not running", icon="ERROR")
            return

        directory = host.blendlets_directory
        row = layout.row()
        row.label(
            text=str(directory) if directory is not None else "",
            icon="FILE_FOLDER",
        )
        row.operator("blendlet.open_directory", text="", icon="FILEBROWSER")

        snapshot = host.status_snapshot()
        blendlets = snapshot["blendlets"]
        header = layout.row()
        header.label(text=f"{len(blendlets)} blendlet(s)")
        header.operator("blendlet.reload_all", text="Reload All", icon="FILE_REFRESH")

        if not blendlets:
            layout.label(text="No blendlets in this directory")
        for entry in blendlets:
            self._draw_blendlet(layout, entry)

        self._draw_project(layout, host)

    def _draw_project(self, layout, host) -> None:
        directory = project.current_directory()
        if directory is None or directory == host.blendlets_directory:
            return
        snapshot = host.project_status_snapshot()
        if snapshot is None or host.project_directory != directory:
            untrusted = sorted(iter_blendlet_files(directory), key=lambda item: item.name)
            if untrusted:
                self._draw_project_header(layout, directory)
                self._draw_untrusted(layout, untrusted)
            return
        self._draw_project_header(layout, directory)
        if not snapshot["blendlets"]:
            layout.label(text="No blendlets in this folder")
        for entry in snapshot["blendlets"]:
            self._draw_blendlet(layout, entry)

    def _draw_project_header(self, layout, directory) -> None:
        layout.separator()
        layout.label(text="Blend File Blendlets")
        layout.label(text=str(directory), icon="FILE_FOLDER")

    def _draw_untrusted(self, layout, files) -> None:
        box = layout.box()
        warning = box.row()
        warning.alert = True
        warning.label(text="Not trusted: blendlets run Python with full access", icon="ERROR")
        for path in files:
            box.label(text=path.name, icon="FILE_SCRIPT")
        buttons = box.row(align=True)
        buttons.operator("blendlet.trust_project", text="Trust for Now").remember = False
        buttons.operator("blendlet.trust_project", text="Trust and Remember").remember = True

    def _draw_blendlet(self, layout, entry: dict) -> None:
        box = layout.box()
        title = box.row()
        title.label(text=entry["name"], icon=_state_icon(entry))
        state = title.row()
        state.enabled = latest_edit_loaded(entry) or entry.get("state") == "reloading"
        if entry.get("state") == "error":
            state.alert = True
            state.enabled = True
        state.label(text=display_state(entry))

        controls = box.row(align=True)
        reload = controls.operator("blendlet.reload", text="Reload", icon="FILE_REFRESH")
        reload.path = entry["path"]
        if entry.get("state") == "disabled":
            enable = controls.operator("blendlet.set_enabled", text="Enable")
            enable.path = entry["path"]
            enable.enabled = True
        else:
            disable = controls.operator("blendlet.set_enabled", text="Disable")
            disable.path = entry["path"]
            disable.enabled = False

        if not entry.get("error"):
            return
        error_box = box.box()
        error_box.alert = True
        for line in error_preview_lines(entry):
            error_box.label(text=line)
        copy = error_box.operator("blendlet.copy_error", text="Copy Error", icon="COPYDOWN")
        copy.path = entry["path"]


CLASSES = (
    BLENDLET_OT_reload,
    BLENDLET_OT_reload_all,
    BLENDLET_OT_set_enabled,
    BLENDLET_OT_open_directory,
    BLENDLET_OT_copy_error,
    BLENDLET_OT_trust_project,
    BLENDLET_OT_forget_trusted_directory,
    BLENDLET_PT_status,
)


def register() -> None:
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
