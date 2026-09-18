from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blendlet.discovery import (
    DEFAULT_DEBOUNCE_SECONDS,
    ChangeKind,
    FileChange,
    PendingFile,
    Watcher,
    fingerprint_file,
    is_blendlet_name,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BlendletNameTests(unittest.TestCase):
    def test_accepts_python_files_and_rejects_hidden_or_other_suffixes(self):
        self.assertTrue(is_blendlet_name("relax.py"))
        self.assertTrue(is_blendlet_name("Relax.PY"))
        self.assertTrue(is_blendlet_name("_helper.py"))
        self.assertFalse(is_blendlet_name(".hidden.py"))
        self.assertFalse(is_blendlet_name("notes.txt"))
        self.assertFalse(is_blendlet_name("tool.py.bak"))
        self.assertFalse(is_blendlet_name(""))


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()
        self.clock = FakeClock()
        self.watcher = Watcher(
            self.directory,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def _stable_poll(self) -> list[FileChange]:
        first = self.watcher.poll()
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        return first + self.watcher.poll()

    def test_existing_python_file_emits_one_created_event(self):
        path = self._write("brush.py", "print(1)\n")
        events = self._stable_poll()
        self.assertEqual(events, [FileChange(ChangeKind.CREATED, path)])
        self.assertEqual(self.watcher.known_files(), (path,))

    def test_unstable_file_is_pending_until_debounce_elapses(self):
        path = self._write("brush.py", "print(1)\n")
        self.assertEqual(self.watcher.poll(), [])
        self.assertEqual(
            self.watcher.pending_files(),
            (PendingFile(path=path, fingerprint=fingerprint_file(path)),),
        )
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(self.watcher.poll(), [FileChange(ChangeKind.CREATED, path)])
        self.assertEqual(self.watcher.pending_files(), ())

    def test_ignores_non_python_and_nested_files(self):
        self._write("readme.txt", "nope\n")
        self._write(".secret.py", "hidden = True\n")
        nested = self.directory / "sub"
        nested.mkdir()
        (nested / "nested.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self._stable_poll(), [])
        self.assertEqual(self.watcher.known_files(), ())

    def test_create_modify_delete_each_produce_one_event(self):
        path = self._write("tool.py", "value = 1\n")
        created = self._stable_poll()
        self.assertEqual(created, [FileChange(ChangeKind.CREATED, path)])

        path.write_text("value = 2\n", encoding="utf-8")
        modified = self._stable_poll()
        self.assertEqual(modified, [FileChange(ChangeKind.MODIFIED, path)])

        path.unlink()
        deleted = self._stable_poll()
        self.assertEqual(deleted, [FileChange(ChangeKind.DELETED, path)])
        self.assertEqual(self.watcher.known_files(), ())

    def test_rapid_writes_collapse_to_one_created_event(self):
        path = self._write("agent.py", "step = 1\n")
        self.assertEqual(self.watcher.poll(), [])
        self.clock.advance(0.1)
        path.write_text("step = 2\n", encoding="utf-8")
        self.assertEqual(self.watcher.poll(), [])
        self.clock.advance(0.1)
        path.write_text("step = 3\n", encoding="utf-8")
        self.assertEqual(self.watcher.poll(), [])
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        events = self.watcher.poll()
        self.assertEqual(events, [FileChange(ChangeKind.CREATED, path)])
        self.assertEqual(path.read_text(encoding="utf-8"), "step = 3\n")

    def test_agent_style_replace_of_known_file_is_one_modified_event(self):
        path = self._write("agent.py", "step = 1\n")
        self._stable_poll()

        path.unlink()
        self.assertEqual(self.watcher.poll(), [])
        self.clock.advance(0.1)
        tmp = self.directory / "agent.py.tmp"
        tmp.write_text("step = 9\n", encoding="utf-8")
        tmp.replace(path)
        self.assertEqual(self.watcher.poll(), [])
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        events = self.watcher.poll()
        self.assertEqual(events, [FileChange(ChangeKind.MODIFIED, path)])
        extra = self._stable_poll()
        self.assertEqual(extra, [])

    def test_create_then_delete_before_debounce_emits_nothing(self):
        path = self._write("flash.py", "transient = True\n")
        self.assertEqual(self.watcher.poll(), [])
        path.unlink()
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(self.watcher.poll(), [])
        self.assertEqual(self.watcher.known_files(), ())

    def test_rewrite_with_identical_content_does_not_emit_modified(self):
        path = self._write("same.py", "value = 1\n")
        self._stable_poll()
        path.write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self._stable_poll(), [])

    def test_unreadable_file_waits_until_it_can_be_hashed(self):
        path = self.directory / "locked.py"
        path.write_text("before\n", encoding="utf-8")
        readable = {"on": False}

        def hasher(candidate: Path) -> str | None:
            if candidate == path and not readable["on"]:
                return None
            return path.read_bytes().hex() if candidate == path else None

        watcher = Watcher(
            self.directory,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
            clock=self.clock,
            read_fingerprint=hasher,
        )
        self.assertEqual(watcher.poll(), [])
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(watcher.poll(), [])

        readable["on"] = True
        self.assertEqual(watcher.poll(), [])
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(watcher.poll(), [FileChange(ChangeKind.CREATED, path)])

    def test_missing_directory_is_empty_not_an_error(self):
        missing = self.directory / "does-not-exist"
        watcher = Watcher(
            missing,
            debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
            clock=self.clock,
        )
        self.assertEqual(watcher.poll(), [])
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(watcher.known_files(), ())

    def test_set_directory_emits_deletes_for_previous_files(self):
        path = self._write("old.py", "old = True\n")
        self._stable_poll()
        other = Path(tempfile.mkdtemp()).resolve()
        events = self.watcher.set_directory(other)
        self.assertEqual(events, [FileChange(ChangeKind.DELETED, path)])
        self.assertEqual(self.watcher.known_files(), ())
        self.assertEqual(self.watcher.directory, other)
