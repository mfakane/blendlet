"""Resolve blendlets directories without importing bpy."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_DIRNAME = "blendlets"
PROJECT_DIRNAME = ".blendlet"


def project_blendlets_directory(blend_filepath: str) -> Path | None:
    """Return the ``.blendlet`` folder beside a saved .blend file.

    An unsaved file (empty path) has no project folder. The folder does not
    need to exist.
    """
    if not blend_filepath:
        return None
    return (Path(blend_filepath).parent / PROJECT_DIRNAME).resolve()


def default_blendlets_directory(*, home: Path | None = None) -> Path:
    base = Path.home() if home is None else home
    return base / _DEFAULT_DIRNAME


def resolve_blendlets_directory(
    configured: str | None,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Return an absolute blendlets directory.

    An empty or whitespace-only preference uses ``~/blendlets``. The path does
    not need to exist; callers decide whether to create it.
    """
    raw = (configured or "").strip()
    if not raw:
        return default_blendlets_directory(home=home).expanduser().resolve()

    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = Path.cwd() if cwd is None else cwd
        path = base / path
    return path.resolve()
