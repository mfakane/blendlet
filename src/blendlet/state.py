"""Host-owned per-blendlet persistent state.

Small config-like values live on the host, not on the blendlet module, so a
hot reload can keep them without migrating Python objects. Identity is the
blendlet path. File deletion and host restart drop the slot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def identity_for(path: Path) -> str:
    """Stable store key for a blendlet path."""
    path = Path(path)
    try:
        path = path.resolve()
    except OSError:
        pass
    return path.as_posix()


class StateStore:
    """In-memory dict of per-blendlet state slots.

    v0.1 storage is process-local. Reloads reuse the slot for the same path.
    Deleting the file or stopping the host forgets it.
    """

    def __init__(self) -> None:
        self._slots: dict[str, dict[str, Any]] = {}

    def for_path(self, path: Path) -> dict[str, Any]:
        key = identity_for(path)
        slot = self._slots.get(key)
        if slot is None:
            slot = {}
            self._slots[key] = slot
        return slot

    def drop(self, path: Path) -> None:
        self._slots.pop(identity_for(path), None)

    def clear(self) -> None:
        self._slots.clear()
