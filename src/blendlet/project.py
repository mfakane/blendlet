"""Blendlets in the ``.blendlet`` folder beside the open .blend file.

A blendlet is arbitrary Python, and a .blend file can come from anywhere, so
the host only watches a project folder after the user trusts it: "Trust for
Now" lasts until the add-on stops, "Trust and Remember" is kept in add-on
preferences. ``sync()`` is the only place that hands a project folder to the
host.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import preferences
from .host import get_host
from .paths import project_blendlets_directory

_session_trusted: set[Path] = set()


def current_directory() -> Path | None:
    """The project folder for the open .blend file, or None if it is unsaved."""
    try:
        filepath = bpy.data.filepath
    except AttributeError:
        # bpy.data is restricted while addon_utils runs register().
        return None
    return project_blendlets_directory(filepath)


def is_trusted(directory: Path) -> bool:
    directory = Path(directory)
    return directory in _session_trusted or directory in preferences.remembered_directories()


def trust(directory: Path, *, remember: bool) -> bool:
    """Trust ``directory`` and load it if it is the current project folder.

    Returns False when remembering is impossible because add-on preferences
    are unavailable; nothing is trusted then.
    """
    directory = Path(directory)
    if remember:
        if not preferences.remember_directory(directory):
            return False
    else:
        _session_trusted.add(directory)
    sync()
    return True


def forget(directory: Path) -> None:
    """Stop trusting ``directory``, unloading its blendlets if they run."""
    directory = Path(directory)
    _session_trusted.discard(directory)
    preferences.forget_directory(directory)
    sync()


def sync() -> None:
    host = get_host()
    if not host.running:
        return
    directory = current_directory()
    if directory is not None and not is_trusted(directory):
        directory = None
    host.set_project_directory(directory)


@persistent
def _on_file_change(*_args) -> None:
    sync()


def _sync_once() -> None:
    sync()
    return None


def _handler_lists() -> tuple[list, ...]:
    # save_post covers "Save As" into a folder that has a .blendlet folder.
    return (bpy.app.handlers.load_post, bpy.app.handlers.save_post)


def register() -> None:
    for handlers in _handler_lists():
        if _on_file_change not in handlers:
            handlers.append(_on_file_change)
    sync()
    # When enabled from Preferences, register() cannot read bpy.data, so look
    # at the open file again once Blender is back in its event loop.
    if not bpy.app.timers.is_registered(_sync_once):
        bpy.app.timers.register(_sync_once, first_interval=0.0)


def unregister() -> None:
    if bpy.app.timers.is_registered(_sync_once):
        bpy.app.timers.unregister(_sync_once)
    for handlers in _handler_lists():
        if _on_file_change in handlers:
            handlers.remove(_on_file_change)
    _session_trusted.clear()
