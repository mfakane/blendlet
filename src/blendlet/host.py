"""Host start/stop lifecycle owned by the Blendlet add-on."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .discovery import (
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    FileChange,
    PendingFile,
    Watcher,
)
from .lifecycle import Blendlet, Lifecycle
from .status import write_status


class Host:
    """Process-wide host for discovery and blendlet module lifecycle.

    ``stop()`` is the cleanup path: it is idempotent, and ``start()`` while
    already running goes through ``stop()`` first so leftover host state cannot
    accumulate across enable/disable cycles. Discovery watching is started and
    stopped with the host; file events load, reload, or unload modules, and
    are also delivered on the Blender timer thread via ``subscribe``.
    Per-blendlet ``ctx.state`` is dropped on ``stop()`` and on file deletion.
    After each poll the host writes ``.blendlet/status.json`` so an external
    agent can observe load success and reload errors from files alone.
    """

    def __init__(self) -> None:
        self._running = False
        self._blendlets_directory: Path | None = None
        self._watcher: Watcher | None = None
        self._lifecycle: Lifecycle | None = None
        self._listeners: list[Callable[[FileChange], None]] = []
        self._timer: Callable[[], float | None] | None = None
        self._poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self._debounce_seconds = DEFAULT_DEBOUNCE_SECONDS
        self._status_signature: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def watching(self) -> bool:
        return self._watcher is not None

    @property
    def blendlets_directory(self) -> Path | None:
        return self._blendlets_directory

    def discovered_files(self) -> tuple[Path, ...]:
        if self._watcher is None:
            return ()
        return self._watcher.known_files()

    def persistent_files(self) -> tuple[Path, ...]:
        if self._lifecycle is None:
            return ()
        return self._lifecycle.persistent_files()

    def loaded_blendlet(self, path: Path) -> Blendlet | None:
        if self._lifecycle is None:
            return None
        return self._lifecycle.get(path)

    def subscribe(self, callback: Callable[[FileChange], None]) -> None:
        self._listeners.append(callback)

    def start(
        self,
        blendlets_directory: Path,
        *,
        clock: Callable[[], float] | None = None,
        debounce_seconds: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        if self._running:
            self.stop()
        directory = Path(blendlets_directory)
        if not directory.is_absolute():
            raise ValueError("blendlets directory must be an absolute path")
        self._blendlets_directory = directory
        if debounce_seconds is not None:
            self._debounce_seconds = debounce_seconds
        if poll_interval is not None:
            self._poll_interval = poll_interval
        self._watcher = Watcher(
            directory,
            debounce_seconds=self._debounce_seconds,
            clock=clock,
        )
        self._lifecycle = Lifecycle()
        self._running = True
        self._status_signature = None
        self._register_timer()
        self._write_status()
        self.poll()

    def stop(self) -> None:
        if not self._running:
            return
        self._unregister_timer()
        if self._lifecycle is not None:
            self._lifecycle.unload_all()
            self._lifecycle = None
        self._write_status(running=False)
        if self._watcher is not None:
            self._watcher.clear()
            self._watcher = None
        self._cleanup()
        self._running = False
        self._status_signature = None

    def set_blendlets_directory(self, blendlets_directory: Path) -> None:
        directory = Path(blendlets_directory)
        if not directory.is_absolute():
            raise ValueError("blendlets directory must be an absolute path")
        if not self._running or self._watcher is None:
            raise RuntimeError("host is not running")
        if directory == self._blendlets_directory:
            return
        previous = self._blendlets_directory
        changes = self._watcher.set_directory(directory)
        if previous is not None and previous != directory:
            self._write_status(directory=previous, running=False, records=(), pending=())
        self._blendlets_directory = directory
        self._status_signature = None
        self._dispatch(changes)
        self.poll()

    def poll(self) -> list[FileChange]:
        if not self._running or self._watcher is None:
            return []
        changes = self._watcher.poll()
        self._dispatch(changes)
        self._write_status()
        return changes

    def is_timer_registered(self) -> bool:
        if self._timer is None:
            return False
        try:
            import bpy
        except ImportError:
            return False
        return bool(bpy.app.timers.is_registered(self._timer))

    def _dispatch(self, changes: list[FileChange]) -> None:
        if self._lifecycle is not None:
            for change in changes:
                self._lifecycle.handle(change)
        for change in changes:
            for listener in list(self._listeners):
                listener(change)

    def _register_timer(self) -> None:
        try:
            import bpy
        except ImportError:
            return
        if self._timer is not None and bpy.app.timers.is_registered(self._timer):
            return

        host = self

        def tick() -> float | None:
            if not host._running:
                return None
            host.poll()
            return host._poll_interval

        self._timer = tick
        bpy.app.timers.register(tick, first_interval=0.0, persistent=True)

    def _unregister_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is None:
            return
        try:
            import bpy
        except ImportError:
            return
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)

    def _cleanup(self) -> None:
        self._blendlets_directory = None

    def _write_status(
        self,
        *,
        directory: Path | None = None,
        running: bool | None = None,
        records: tuple[Blendlet, ...] | None = None,
        pending: tuple[PendingFile, ...] | None = None,
    ) -> None:
        target = directory if directory is not None else self._blendlets_directory
        if target is None:
            return
        is_running = self._running if running is None else running
        if records is None:
            if is_running and self._lifecycle is not None:
                records = self._lifecycle.blendlets()
            else:
                records = ()
        if pending is None:
            if is_running and self._watcher is not None:
                pending = self._watcher.pending_files()
            else:
                pending = ()
        try:
            _path, signature, _written = write_status(
                target,
                running=is_running,
                records=records,
                pending=pending,
                now=datetime.now(timezone.utc),
                previous_signature=self._status_signature if directory is None else None,
            )
        except OSError:
            return
        if directory is None:
            self._status_signature = signature


_host: Host | None = None


def get_host() -> Host:
    global _host
    if _host is None:
        _host = Host()
    return _host


def reset_host() -> None:
    """Drop the process singleton. Tests use this; the add-on does not."""
    global _host
    if _host is not None and _host.running:
        _host.stop()
    _host = None
