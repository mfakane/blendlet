"""Discover top-level blendlet files and emit debounced create/modify/delete events.

The watcher is bpy-free so unit tests can drive it with a fake clock. The host
polls it from a ``bpy.app.timers`` callback; that is the only Blender coupling.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

DEFAULT_DEBOUNCE_SECONDS = 0.4
DEFAULT_POLL_INTERVAL_SECONDS = 0.2

_UNREADABLE = object()


class ChangeKind(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class FileChange:
    kind: ChangeKind
    path: Path


@dataclass(frozen=True)
class PendingFile:
    """A blendlet whose fingerprint has not been stable long enough to emit."""

    path: Path
    fingerprint: str | None


@dataclass
class _Pending:
    fingerprint: object
    since: float


def is_blendlet_name(name: str) -> bool:
    """Return True for a top-level blendlet filename.

    Hidden names (``.foo.py``) are ignored. The check is case-insensitive for
    the ``.py`` suffix so Windows editor casing does not drop a tool.
    """
    return bool(name) and not name.startswith(".") and name.lower().endswith(".py")


def fingerprint_file(path: Path) -> str | None:
    """Return a content hash, or None if the file cannot be read completely."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def iter_blendlet_files(directory: Path) -> list[Path]:
    """List top-level ``.py`` files. Missing/unreadable directories are empty."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []

    found: list[Path] = []
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if not is_file or not is_blendlet_name(entry.name):
            continue
        found.append(entry)
    return found


class Watcher:
    """Track stable blendlet files under one directory.

    A change is emitted only after the same fingerprint (or absence) has been
    observed for ``debounce_seconds``. Rapid writes collapse to one event.
    A known file that disappears and reappears within the debounce window is a
    modification, not a delete plus create.
    """

    def __init__(
        self,
        directory: Path,
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        clock: Callable[[], float] | None = None,
        read_fingerprint: Callable[[Path], str | None] | None = None,
    ) -> None:
        if debounce_seconds < 0:
            raise ValueError("debounce_seconds must be >= 0")
        self._directory = Path(directory)
        self._debounce_seconds = debounce_seconds
        self._clock = clock or time.monotonic
        self._read_fingerprint = read_fingerprint or fingerprint_file
        self._emitted: dict[Path, str] = {}
        self._pending: dict[Path, _Pending] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def known_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._emitted, key=lambda path: path.as_posix()))

    def pending_files(self) -> tuple[PendingFile, ...]:
        items: list[PendingFile] = []
        for path in sorted(self._pending, key=lambda item: item.as_posix()):
            fingerprint = self._pending[path].fingerprint
            if fingerprint is _UNREADABLE or not isinstance(fingerprint, str):
                items.append(PendingFile(path=path, fingerprint=None))
            else:
                items.append(PendingFile(path=path, fingerprint=fingerprint))
        return tuple(items)

    def clear(self) -> None:
        self._emitted.clear()
        self._pending.clear()

    def acknowledge(self, path: Path) -> None:
        """Treat the current on-disk fingerprint as already emitted.

        Manual reload/enable uses this so the next poll does not emit a second
        create/modify for the same contents.
        """
        path = Path(path)
        self._pending.pop(path, None)
        fingerprint = self._read_fingerprint(path)
        if fingerprint is None:
            self._emitted.pop(path, None)
        else:
            self._emitted[path] = fingerprint

    def set_directory(self, directory: Path) -> list[FileChange]:
        """Retarget the watcher. Currently known files are deleted immediately."""
        events = [
            FileChange(kind=ChangeKind.DELETED, path=path)
            for path in self.known_files()
        ]
        self.clear()
        self._directory = Path(directory)
        return events

    def poll(self, now: float | None = None) -> list[FileChange]:
        current_time = self._clock() if now is None else now
        listed, observed, unreadable = self._scan()
        interesting = set(self._emitted) | set(self._pending) | listed
        events: list[FileChange] = []

        for path in sorted(interesting, key=lambda item: item.as_posix()):
            if path in unreadable:
                self._note_unreadable(path, current_time)
                continue

            fingerprint: object | None = observed.get(path) if path in listed else None
            event = self._advance(path, fingerprint, current_time)
            if event is not None:
                events.append(event)
        return events

    def _scan(self) -> tuple[set[Path], dict[Path, str], set[Path]]:
        listed = set(iter_blendlet_files(self._directory))
        observed: dict[Path, str] = {}
        unreadable: set[Path] = set()
        for path in listed:
            fingerprint = self._read_fingerprint(path)
            if fingerprint is None:
                unreadable.add(path)
            else:
                observed[path] = fingerprint
        return listed, observed, unreadable

    def _note_unreadable(self, path: Path, now: float) -> None:
        pending = self._pending.get(path)
        if pending is None or pending.fingerprint is not _UNREADABLE:
            self._pending[path] = _Pending(fingerprint=_UNREADABLE, since=now)

    def _advance(
        self,
        path: Path,
        fingerprint: object | None,
        now: float,
    ) -> FileChange | None:
        emitted = self._emitted.get(path)
        pending = self._pending.get(path)

        if fingerprint is not None and fingerprint == emitted and pending is None:
            return None

        if pending is None:
            self._pending[path] = _Pending(fingerprint=fingerprint, since=now)
            return None

        if pending.fingerprint is _UNREADABLE or pending.fingerprint != fingerprint:
            self._pending[path] = _Pending(fingerprint=fingerprint, since=now)
            return None

        if now - pending.since < self._debounce_seconds:
            return None

        del self._pending[path]
        return self._commit(path, fingerprint, emitted)

    def _commit(
        self,
        path: Path,
        fingerprint: object | None,
        emitted: str | None,
    ) -> FileChange | None:
        if fingerprint is None:
            if emitted is None:
                return None
            del self._emitted[path]
            return FileChange(kind=ChangeKind.DELETED, path=path)

        assert isinstance(fingerprint, str)
        if emitted is None:
            self._emitted[path] = fingerprint
            return FileChange(kind=ChangeKind.CREATED, path=path)
        if emitted != fingerprint:
            self._emitted[path] = fingerprint
            return FileChange(kind=ChangeKind.MODIFIED, path=path)
        return None
