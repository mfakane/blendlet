"""Blendlet host add-on: start/stop, discovery, and blendlet module lifecycle."""

from __future__ import annotations

from . import host, paths


def register() -> None:
    from . import preferences, ui

    preferences.register()
    ui.register()
    directory = paths.resolve_blendlets_directory(
        preferences.configured_blendlets_directory()
    )
    host.get_host().start(directory)


def unregister() -> None:
    from . import preferences, ui

    try:
        host.get_host().stop()
    finally:
        ui.unregister()
        preferences.unregister()
