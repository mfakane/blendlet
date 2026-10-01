"""Host start/stop lifecycle owned by the Blendlet add-on."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .discovery import (
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    ChangeKind,
    FileChange,
    PendingFile,
    Watcher,
)
from .lifecycle import Blendlet, Lifecycle
from .status import build_status, write_status


@dataclass
class _Source:
    """One watched blendlets directory and its status-file bookkeeping."""

    directory: Path
    watcher: Watcher
    status_signature: str | None = None


class Host:
    """Process-wide host for discovery and blendlet module lifecycle.

    ``stop()`` is the cleanup path: it is idempotent, and ``start()`` while
    already running goes through ``stop()`` first so leftover host state cannot
    accumulate across enable/disable cycles. Discovery watching is started and
    stopped with the host; file events load, reload, or unload modules, and
    are also delivered on the Blender timer thread via ``subscribe``.
    Per-blendlet ``ctx.state`` is dropped on ``stop()`` and on file deletion.
    After each poll the host writes ``.blendlet-status/status.json`` so an external
    agent can observe load success and reload errors from files alone.
    Disabled blendlets stay discovered but are not auto-loaded until enabled
    or manually reloaded. Disable is in-memory for the current host run.

    Besides the user blendlets directory, the host can watch one project
    directory (the ``.blendlet`` folder beside the open .blend file). The host
    does not decide trust: callers pass only a directory the user trusted.
    Each watched directory gets its own ``.blendlet-status/status.json``.
    """

    def __init__(self) -> None:
        self._running = False
        self._user: _Source | None = None
        self._project: _Source | None = None
        self._project_request: Path | None = None
        self._lifecycle: Lifecycle | None = None
        self._listeners: list[Callable[[FileChange], None]] = []
        self._timer: Callable[[], float | None] | None = None
        self._poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self._debounce_seconds = DEFAULT_DEBOUNCE_SECONDS
        self._clock: Callable[[], float] | None = None
        self._disabled: set[Path] = set()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def watching(self) -> bool:
        return self._user is not None

    @property
    def blendlets_directory(self) -> Path | None:
        return None if self._user is None else self._user.directory

    @property
    def project_directory(self) -> Path | None:
        """The trusted project directory being watched, if any.

        A requested project directory equal to the user directory is not
        watched twice, so this is None in that case.
        """
        return None if self._project is None else self._project.directory

    def discovered_files(self) -> tuple[Path, ...]:
        files = [path for source in self._sources() for path in source.watcher.known_files()]
        return tuple(sorted(files, key=lambda path: path.as_posix()))

    def persistent_files(self) -> tuple[Path, ...]:
        if self._lifecycle is None:
            return ()
        return self._lifecycle.persistent_files()

    def loaded_blendlet(self, path: Path) -> Blendlet | None:
        if self._lifecycle is None:
            return None
        return self._lifecycle.get(path)

    def disabled_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._disabled, key=lambda path: path.as_posix()))

    def is_disabled(self, path: Path) -> bool:
        return Path(path) in self._disabled

    def subscribe(self, callback: Callable[[FileChange], None]) -> None:
        self._listeners.append(callback)

    def status_snapshot(self) -> dict:
        """Status of the user blendlets directory."""
        return self._snapshot(self._user)

    def project_status_snapshot(self) -> dict | None:
        """Status of the watched project directory, or None when there is none."""
        if self._project is None:
            return None
        return self._snapshot(self._project)

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
        if debounce_seconds is not None:
            self._debounce_seconds = debounce_seconds
        if poll_interval is not None:
            self._poll_interval = poll_interval
        self._clock = clock
        self._user = self._new_source(directory)
        self._lifecycle = Lifecycle()
        self._disabled = set()
        self._running = True
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
        for source in self._sources():
            _write_stopped_status(source.directory)
            source.watcher.clear()
        self._user = None
        self._project = None
        self._project_request = None
        self._disabled = set()
        self._running = False

    def set_blendlets_directory(self, blendlets_directory: Path) -> None:
        directory = Path(blendlets_directory)
        if not directory.is_absolute():
            raise ValueError("blendlets directory must be an absolute path")
        if not self._running or self._user is None:
            raise RuntimeError("host is not running")
        if directory == self._user.directory:
            return
        if self.project_directory == directory:
            self._detach_project()
        previous = self._user.directory
        changes = self._user.watcher.set_directory(directory)
        _write_stopped_status(previous)
        self._user.directory = directory
        self._user.status_signature = None
        self._dispatch(changes)
        self._sync_project()
        self.poll()

    def set_project_directory(self, directory: Path | None) -> None:
        """Watch a trusted project directory, or stop watching it with None.

        Leaving a project directory unloads its blendlets and drops their
        ``ctx.state``, like deleting the files.
        """
        if directory is not None:
            directory = Path(directory)
            if not directory.is_absolute():
                raise ValueError("project directory must be an absolute path")
        self._require_running()
        if directory == self._project_request:
            return
        self._project_request = directory
        self._sync_project()
        self.poll()

    def poll(self) -> list[FileChange]:
        if not self._running:
            return []
        changes: list[FileChange] = []
        for source in self._sources():
            changes.extend(source.watcher.poll())
        self._dispatch(changes)
        self._write_status()
        _tag_ui_redraw()
        return changes

    def reload_now(self, path: Path) -> Blendlet | None:
        """Load current disk contents immediately, re-enabling the blendlet."""
        self._require_running()
        record = self._reload_path(Path(path))
        self._write_status()
        _tag_ui_redraw()
        return record

    def reload_all(self) -> None:
        """Reload every discovered blendlet from disk, re-enabling each one."""
        self._require_running()
        assert self._lifecycle is not None
        paths = set(self.discovered_files())
        for source in self._sources():
            for item in source.watcher.pending_files():
                paths.add(item.path)
        self._disabled.clear()
        for path in sorted(paths, key=lambda item: item.as_posix()):
            self._reload_path(path)
        self._write_status()
        _tag_ui_redraw()

    def disable(self, path: Path) -> None:
        """Unload a blendlet and ignore later create/modify events for it."""
        self._require_running()
        assert self._lifecycle is not None
        path = Path(path)
        self._disabled.add(path)
        self._lifecycle.unload(path)
        self._write_status()
        _tag_ui_redraw()

    def enable(self, path: Path) -> Blendlet | None:
        """Load a previously disabled blendlet from disk."""
        self._require_running()
        path = Path(path)
        self._disabled.discard(path)
        record = self._reload_path(path) if path.is_file() else None
        self._write_status()
        _tag_ui_redraw()
        return record

    def is_timer_registered(self) -> bool:
        if self._timer is None:
            return False
        try:
            import bpy
        except ImportError:
            return False
        return bool(bpy.app.timers.is_registered(self._timer))

    def _require_running(self) -> None:
        if not self._running or self._lifecycle is None:
            raise RuntimeError("host is not running")

    def _sources(self) -> tuple[_Source, ...]:
        return tuple(source for source in (self._user, self._project) if source is not None)

    def _source_for(self, path: Path) -> _Source | None:
        """The project source for files directly in it; the user source otherwise."""
        if self._project is not None and Path(path).parent == self._project.directory:
            return self._project
        return self._user

    def _new_source(self, directory: Path) -> _Source:
        watcher = Watcher(
            directory,
            debounce_seconds=self._debounce_seconds,
            clock=self._clock,
        )
        return _Source(directory=directory, watcher=watcher)

    def _sync_project(self) -> None:
        wanted = self._project_request
        if self._user is not None and wanted == self._user.directory:
            wanted = None
        if wanted == self.project_directory:
            return
        self._detach_project()
        if wanted is not None:
            self._project = self._new_source(wanted)

    def _detach_project(self) -> None:
        source = self._project
        if source is None:
            return
        changes = [
            FileChange(kind=ChangeKind.DELETED, path=path)
            for path in source.watcher.known_files()
        ]
        source.watcher.clear()
        self._dispatch(changes)
        self._disabled = {path for path in self._disabled if path.parent != source.directory}
        self._project = None
        _write_stopped_status(source.directory)

    def _reload_path(self, path: Path) -> Blendlet | None:
        assert self._lifecycle is not None
        path = Path(path)
        self._disabled.discard(path)
        source = self._source_for(path)
        if source is not None:
            source.watcher.acknowledge(path)
        if not path.is_file():
            self._lifecycle.unload(path, forget_state=True)
            return None
        return self._lifecycle.reload(path)

    def _dispatch(self, changes: list[FileChange]) -> None:
        if self._lifecycle is not None:
            for change in changes:
                if change.kind is ChangeKind.DELETED:
                    self._disabled.discard(change.path)
                    self._lifecycle.handle(change)
                    continue
                if change.path in self._disabled:
                    continue
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

    def _source_parts(
        self,
        source: _Source,
    ) -> tuple[tuple[Blendlet, ...], tuple[PendingFile, ...], tuple[Path, ...]]:
        assert self._lifecycle is not None
        records = tuple(
            record
            for record in self._lifecycle.blendlets()
            if self._source_for(record.path) is source
        )
        disabled = tuple(
            path for path in self.disabled_files() if self._source_for(path) is source
        )
        return records, source.watcher.pending_files(), disabled

    def _snapshot(self, source: _Source | None) -> dict:
        now = datetime.now(timezone.utc)
        if not self._running or source is None or self._lifecycle is None:
            return build_status(blendlets_directory=Path(), running=False, now=now)
        records, pending, disabled = self._source_parts(source)
        return build_status(
            blendlets_directory=source.directory,
            running=True,
            records=records,
            pending=pending,
            disabled=disabled,
            now=now,
        )

    def _write_status(self) -> None:
        if not self._running or self._lifecycle is None:
            return
        for source in self._sources():
            records, pending, disabled = self._source_parts(source)
            try:
                _path, signature, _written = write_status(
                    source.directory,
                    running=True,
                    records=records,
                    pending=pending,
                    disabled=disabled,
                    now=datetime.now(timezone.utc),
                    previous_signature=source.status_signature,
                )
            except OSError:
                continue
            source.status_signature = signature


def _write_stopped_status(directory: Path) -> None:
    try:
        write_status(directory, running=False, now=datetime.now(timezone.utc))
    except OSError:
        pass


def _tag_ui_redraw() -> None:
    try:
        import bpy
    except ImportError:
        return
    window_manager = getattr(getattr(bpy, "context", None), "window_manager", None)
    if window_manager is None:
        return
    for window in getattr(window_manager, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []):
            if area.type == "VIEW_3D":
                area.tag_redraw()


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
