from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from blendlet.discovery import ChangeKind, DEFAULT_DEBOUNCE_SECONDS, FileChange
from blendlet.host import Host, get_host, reset_host
from blendlet.lifecycle import module_name_for


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class HostLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = Host()
        self.directory = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self) -> None:
        if self.host.running:
            self.host.stop()
        reset_host()

    def test_start_exposes_running_directory(self):
        self.host.start(self.directory)
        self.assertTrue(self.host.running)
        self.assertEqual(self.host.blendlets_directory, self.directory)

    def test_stop_clears_running_state_and_directory(self):
        self.host.start(self.directory)
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_stop_is_idempotent(self):
        self.host.start(self.directory)
        self.host.stop()
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_start_while_running_goes_through_cleanup(self):
        other = Path(tempfile.mkdtemp()).resolve()
        self.host.start(self.directory)
        self.host.start(other)
        self.assertTrue(self.host.running)
        self.assertEqual(self.host.blendlets_directory, other)
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_start_rejects_relative_directory(self):
        with self.assertRaises(ValueError):
            self.host.start(Path("relative-blendlets"))
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_set_directory_while_running(self):
        other = Path(tempfile.mkdtemp()).resolve()
        self.host.start(self.directory)
        self.host.set_blendlets_directory(other)
        self.assertEqual(self.host.blendlets_directory, other)
        self.assertTrue(self.host.running)

    def test_set_directory_while_stopped_raises(self):
        with self.assertRaises(RuntimeError):
            self.host.set_blendlets_directory(self.directory)

    def test_get_host_returns_process_singleton(self):
        reset_host()
        first = get_host()
        second = get_host()
        self.assertIs(first, second)
        first.start(self.directory)
        self.assertTrue(second.running)
        reset_host()
        self.assertFalse(first.running)
        self.assertIsNot(get_host(), first)


class HostDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.host = Host()
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.events: list[FileChange] = []
        self.host.subscribe(self.events.append)
        self.host.start(
            self.directory,
            clock=self.clock,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        )

    def tearDown(self) -> None:
        if self.host.running:
            self.host.stop()
        reset_host()

    def _stable_poll(self) -> list[FileChange]:
        first = self.host.poll()
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        return first + self.host.poll()

    def test_start_discovers_existing_files_and_notifies_subscribers(self):
        self.host.stop()
        self.events.clear()
        path = self.directory / "startup.py"
        path.write_text("ready = True\n", encoding="utf-8")
        self.host.start(
            self.directory,
            clock=self.clock,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        )
        events = self._stable_poll()
        self.assertEqual(events, [FileChange(ChangeKind.CREATED, path)])
        self.assertEqual(self.events, events)
        self.assertEqual(self.host.discovered_files(), (path,))
        self.assertTrue(self.host.watching)

    def test_stop_clears_discovery_and_ignores_later_file_changes(self):
        path = self.directory / "gone.py"
        path.write_text("x = 1\n", encoding="utf-8")
        self._stable_poll()
        self.events.clear()
        self.host.stop()
        self.assertFalse(self.host.watching)
        self.assertEqual(self.host.discovered_files(), ())
        path.write_text("x = 2\n", encoding="utf-8")
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(self.host.poll(), [])
        self.assertEqual(self.events, [])

    def test_changing_directory_deletes_old_files_and_watches_the_new_one(self):
        old = self.directory / "old.py"
        old.write_text("old = True\n", encoding="utf-8")
        self._stable_poll()
        self.events.clear()

        other = Path(tempfile.mkdtemp()).resolve()
        new = other / "new.py"
        new.write_text("new = True\n", encoding="utf-8")
        self.host.set_blendlets_directory(other)
        self.assertEqual(self.host.blendlets_directory, other)
        self.assertEqual(
            [event.kind for event in self.events],
            [ChangeKind.DELETED],
        )
        self.assertEqual(self.events[0].path, old)

        self.events.clear()
        events = self._stable_poll()
        self.assertEqual(events, [FileChange(ChangeKind.CREATED, new)])
        self.assertEqual(self.host.discovered_files(), (new,))

    def test_stop_does_not_require_bpy_timer_unregistration(self):
        self.assertFalse(self.host.is_timer_registered())
        self.host.stop()
        self.assertFalse(self.host.is_timer_registered())
        self.assertFalse(self.host.watching)


PERSISTENT = """\
from pathlib import Path
LOG = Path(__file__).with_suffix(".log")

def _append(line: str) -> None:
    previous = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    LOG.write_text(previous + line + "\\n", encoding="utf-8")

def register(ctx):
    _append("register:" + ctx.name)

def unregister(ctx):
    _append("unregister:" + ctx.name)
"""

TRANSIENT = """\
from pathlib import Path
OUT = Path(__file__).with_suffix(".out")

def run(ctx):
    previous = int(OUT.read_text(encoding="utf-8")) if OUT.exists() else 0
    OUT.write_text(str(previous + 1), encoding="utf-8")
"""


def _log_lines(path: Path) -> list[str]:
    log = path.with_suffix(".log")
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


class HostModuleLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.host = Host()
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.host.start(
            self.directory,
            clock=self.clock,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        )

    def tearDown(self) -> None:
        if self.host.running:
            self.host.stop()
        reset_host()

    def _stable_poll(self) -> list[FileChange]:
        first = self.host.poll()
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        return first + self.host.poll()

    def test_new_persistent_file_loads_automatically(self):
        path = self.directory / "tool.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        record = self.host.loaded_blendlet(path)
        self.assertIsNotNone(record)
        self.assertTrue(record.persistent)
        self.assertEqual(_log_lines(path), ["register:tool"])
        self.assertEqual(self.host.persistent_files(), (path,))

    def test_modified_file_is_reimported(self):
        path = self.directory / "tool.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        path.write_text(PERSISTENT + "\nMARKER = 1\n", encoding="utf-8")
        self._stable_poll()
        record = self.host.loaded_blendlet(path)
        self.assertEqual(record.module.MARKER, 1)
        self.assertEqual(
            _log_lines(path),
            ["register:tool", "unregister:tool", "register:tool"],
        )

    def test_deleted_file_unloads(self):
        path = self.directory / "tool.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        name = self.host.loaded_blendlet(path).module_name
        path.unlink()
        self._stable_poll()
        self.assertIsNone(self.host.loaded_blendlet(path))
        self.assertEqual(self.host.persistent_files(), ())
        self.assertNotIn(name, sys.modules)
        self.assertEqual(_log_lines(path), ["register:tool", "unregister:tool"])

    def test_transient_run_does_not_become_a_permanent_addon(self):
        path = self.directory / "once.py"
        path.write_text(TRANSIENT, encoding="utf-8")
        self._stable_poll()
        record = self.host.loaded_blendlet(path)
        self.assertIsNotNone(record)
        self.assertFalse(record.persistent)
        self.assertEqual((path.with_suffix(".out")).read_text(encoding="utf-8"), "1")
        self.assertEqual(self.host.persistent_files(), ())
        self.assertNotIn(module_name_for(path), sys.modules)

    def test_stop_unregisters_persistent_tools(self):
        path = self.directory / "tool.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        name = self.host.loaded_blendlet(path).module_name
        self.host.stop()
        self.assertEqual(self.host.persistent_files(), ())
        self.assertIsNone(self.host.loaded_blendlet(path))
        self.assertNotIn(name, sys.modules)
        self.assertEqual(_log_lines(path), ["register:tool", "unregister:tool"])

