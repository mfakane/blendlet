"""Blendlet host add-on: start/stop lifecycle and blendlets directory config."""

from __future__ import annotations

from . import host, paths


def register() -> None:
    from . import preferences

    preferences.register()
    directory = paths.resolve_blendlets_directory(
        preferences.configured_blendlets_directory()
    )
    host.get_host().start(directory)


def unregister() -> None:
    from . import preferences

    try:
        host.get_host().stop()
    finally:
        preferences.unregister()
