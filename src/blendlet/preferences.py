"""Add-on preferences for the blendlets directory and trusted project folders."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import AddonPreferences, PropertyGroup

from .paths import resolve_blendlets_directory


def _on_blendlets_directory_update(self, _context) -> None:
    from .host import get_host

    host = get_host()
    if not host.running:
        return
    host.set_blendlets_directory(resolve_blendlets_directory(self.blendlets_directory))


class BlendletTrustedDirectory(PropertyGroup):
    path: StringProperty(name="Path", subtype="DIR_PATH")


class BlendletPreferences(AddonPreferences):
    bl_idname = __package__

    blendlets_directory: StringProperty(
        name="Blendlets Directory",
        description="Directory of blendlet Python files. Leave empty to use ~/blendlets",
        subtype="DIR_PATH",
        default="",
        update=_on_blendlets_directory_update,
    )
    trusted_directories: CollectionProperty(type=BlendletTrustedDirectory)

    def draw(self, _context) -> None:
        layout = self.layout
        layout.prop(self, "blendlets_directory")
        resolved = resolve_blendlets_directory(self.blendlets_directory)
        layout.label(text=f"Resolved: {resolved}")

        box = layout.box()
        box.label(text="Trusted Project Folders")
        if not self.trusted_directories:
            box.label(text="None")
        for item in self.trusted_directories:
            row = box.row()
            row.label(text=item.path, icon="FILE_FOLDER")
            forget = row.operator("blendlet.forget_trusted_directory", text="", icon="X")
            forget.path = item.path


def get_preferences(context=None) -> BlendletPreferences | None:
    ctx = context or bpy.context
    addon = ctx.preferences.addons.get(__package__)
    if addon is None:
        return None
    return addon.preferences


def configured_blendlets_directory(context=None) -> str:
    prefs = get_preferences(context)
    if prefs is None:
        return ""
    return prefs.blendlets_directory


def remembered_directories(context=None) -> tuple[Path, ...]:
    prefs = get_preferences(context)
    if prefs is None:
        return ()
    return tuple(Path(item.path) for item in prefs.trusted_directories)


def remember_directory(directory: Path, context=None) -> bool:
    """Persist trust for ``directory``. False when preferences are unavailable."""
    prefs = get_preferences(context)
    if prefs is None:
        return False
    if Path(directory) not in remembered_directories(context):
        prefs.trusted_directories.add().path = str(directory)
        _mark_dirty(context)
    return True


def forget_directory(directory: Path, context=None) -> None:
    prefs = get_preferences(context)
    if prefs is None:
        return
    for index, item in enumerate(prefs.trusted_directories):
        if Path(item.path) == Path(directory):
            prefs.trusted_directories.remove(index)
            _mark_dirty(context)
            return


def _mark_dirty(context=None) -> None:
    # Python edits to add-on preferences do not flag them as changed, so
    # auto-save would otherwise skip them.
    (context or bpy.context).preferences.is_dirty = True


CLASSES = (
    BlendletTrustedDirectory,
    BlendletPreferences,
)


def register() -> None:
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
