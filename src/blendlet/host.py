"""Host start/stop lifecycle owned by the Blendlet add-on."""

from __future__ import annotations

from pathlib import Path


class Host:
    """Process-wide host that later tickets will attach watching and loading to.

    ``stop()`` is the cleanup path: it is idempotent, and ``start()`` while
    already running goes through ``stop()`` first so leftover host state cannot
    accumulate across enable/disable cycles.
    """

    def __init__(self) -> None:
        self._running = False
        self._blendlets_directory: Path | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def blendlets_directory(self) -> Path | None:
        return self._blendlets_directory

    def start(self, blendlets_directory: Path) -> None:
        if self._running:
            self.stop()
        directory = Path(blendlets_directory)
        if not directory.is_absolute():
            raise ValueError("blendlets directory must be an absolute path")
        self._blendlets_directory = directory
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._cleanup()
        self._running = False

    def set_blendlets_directory(self, blendlets_directory: Path) -> None:
        directory = Path(blendlets_directory)
        if not directory.is_absolute():
            raise ValueError("blendlets directory must be an absolute path")
        if not self._running:
            raise RuntimeError("host is not running")
        self._blendlets_directory = directory

    def _cleanup(self) -> None:
        self._blendlets_directory = None


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
