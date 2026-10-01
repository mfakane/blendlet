"""Blendlet host add-on: start/stop, discovery, and blendlet module lifecycle."""

from __future__ import annotations

from . import host, paths


def register() -> None:
    from . import preferences, project, ui

    preferences.register()
    ui.register()
    directory = paths.resolve_blendlets_directory(
        preferences.configured_blendlets_directory()
    )
    host.get_host().start(directory)
    project.register()


def unregister() -> None:
    from . import preferences, project, ui

    try:
        host.get_host().stop()
    finally:
        project.unregister()
        ui.unregister()
        preferences.unregister()
