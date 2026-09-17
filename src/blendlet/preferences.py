"""Add-on preferences for the blendlets directory."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import AddonPreferences

from .paths import resolve_blendlets_directory


def _on_blendlets_directory_update(self, _context) -> None:
    from .host import get_host

    host = get_host()
    if not host.running:
        return
    host.set_blendlets_directory(resolve_blendlets_directory(self.blendlets_directory))


class BlendletPreferences(AddonPreferences):
    bl_idname = __package__

    blendlets_directory: StringProperty(
        name="Blendlets Directory",
        description="Directory of blendlet Python files. Leave empty to use ~/blendlets",
        subtype="DIR_PATH",
        default="",
        update=_on_blendlets_directory_update,
    )

    def draw(self, _context) -> None:
        layout = self.layout
        layout.prop(self, "blendlets_directory")
        resolved = resolve_blendlets_directory(self.blendlets_directory)
        layout.label(text=f"Resolved: {resolved}")


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


def register() -> None:
    if not BlendletPreferences.is_registered:
        bpy.utils.register_class(BlendletPreferences)


def unregister() -> None:
    if BlendletPreferences.is_registered:
        bpy.utils.unregister_class(BlendletPreferences)
