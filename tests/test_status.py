from __future__ import annotations

import hashlib
import json
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blendlet.context import Context
from blendlet.discovery import (
    DEFAULT_DEBOUNCE_SECONDS,
    PendingFile,
    fingerprint_file,
)
from blendlet.host import Host, reset_host
from blendlet.lifecycle import Blendlet, Lifecycle
from blendlet.status import (
    SCHEMA_VERSION,
    atomic_write_text,
    build_status,
    error_file,
    status_file,
    write_status,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_status(directory: Path) -> dict:
    path = status_file(directory)
    return json.loads(path.read_text(encoding="utf-8"))


def _entry(payload: dict, name: str) -> dict:
    matches = [item for item in payload["blendlets"] if item["name"] == name]
    if len(matches) != 1:
        raise AssertionError(f"expected one {name!r} entry, got {payload['blendlets']}")
    return matches[0]


PERSISTENT = """\
def register(ctx):
    ctx.ok = True
"""

SYNTAX_ERROR = "def register(ctx):\n    $$$\n"

RAISING_REGISTER = """\
def register(ctx):
    raise RuntimeError("boom")
"""

TRANSIENT = """\
from pathlib import Path

def run(ctx):
    Path(__file__).with_suffix(".out").write_text("ran", encoding="utf-8")
"""


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_replace_leaves_complete_text_and_no_temp_file(self):
        path = self.directory / "status.json"
        atomic_write_text(path, '{"ok": true}\n')
        self.assertEqual(path.read_text(encoding="utf-8"), '{"ok": true}\n')
        atomic_write_text(path, '{"ok": false, "n": 2}\n')
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": False, "n": 2})
        leftovers = [
            item for item in self.directory.iterdir() if item.suffix == ".tmp" or ".tmp" in item.name
        ]
        self.assertEqual(leftovers, [])


class StatusSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()
        self.now = datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _record(self, name: str, **fields) -> Blendlet:
        path = self.directory / name
        record = Blendlet(path=path, context=Context(path), **fields)
        return record

    def test_stopped_host_has_empty_blendlets(self):
        record = self._record(
            "brush.py",
            persistent=True,
            module=types.ModuleType("keep"),
        )
        payload = build_status(
            blendlets_directory=self.directory,
            running=False,
            records=(record,),
            now=self.now,
        )
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertFalse(payload["host"]["running"])
        self.assertIsNone(payload["host"]["blendlets_directory"])
        self.assertEqual(payload["blendlets"], [])

    def test_last_good_error_is_distinct_from_a_dead_failure(self):
        active = self._record(
            "brush.py",
            persistent=True,
            module=types.ModuleType("last_good"),
            error=SyntaxError("bad"),
            error_traceback="SyntaxError: bad",
            source_hash="aaa",
            attempted_hash="bbb",
            generation=1,
        )
        dead = self._record(
            "once.py",
            persistent=False,
            error=RuntimeError("boom"),
            error_traceback="RuntimeError: boom",
            attempted_hash="ccc",
        )
        payload = build_status(
            blendlets_directory=self.directory,
            running=True,
            records=(active, dead),
            now=self.now,
        )
        kept = _entry(payload, "brush")
        failed = _entry(payload, "once")
        self.assertEqual(kept["state"], "error")
        self.assertTrue(kept["active"])
        self.assertTrue(kept["last_good"])
        self.assertEqual(kept["file_hash"], "bbb")
        self.assertEqual(kept["active_hash"], "aaa")
        self.assertEqual(kept["error"]["type"], "SyntaxError")
        self.assertEqual(kept["error"]["file"], ".blendlet/errors/brush.txt")
        self.assertEqual(failed["state"], "error")
        self.assertFalse(failed["active"])
        self.assertFalse(failed["last_good"])
        self.assertIsNone(failed["active_hash"])

    def test_pending_file_is_reloading_before_it_is_loaded(self):
        path = self.directory / "new.py"
        payload = build_status(
            blendlets_directory=self.directory,
            running=True,
            pending=(PendingFile(path=path, fingerprint="abc"),),
            now=self.now,
        )
        entry = _entry(payload, "new")
        self.assertEqual(entry["state"], "reloading")
        self.assertEqual(entry["file_hash"], "abc")
        self.assertFalse(entry["active"])
        self.assertFalse(entry["last_good"])


class AgentReadableStatusTests(unittest.TestCase):
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

    def _stable_poll(self) -> None:
        self.host.poll()
        self.clock.advance(DEFAULT_DEBOUNCE_SECONDS)
        self.host.poll()

    def test_start_writes_running_status_before_any_blendlet(self):
        payload = _read_status(self.directory)
        self.assertTrue(payload["host"]["running"])
        self.assertEqual(payload["host"]["blendlets_directory"], str(self.directory))
        self.assertEqual(payload["blendlets"], [])
        self.assertTrue(status_file(self.directory).is_file())

    def test_agent_can_see_a_successful_load_from_the_status_file(self):
        path = self.directory / "brush.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self.host.poll()
        pending = _read_status(self.directory)
        self.assertEqual(_entry(pending, "brush")["state"], "reloading")
        self.assertEqual(_entry(pending, "brush")["file_hash"], _sha256(path))
        self._stable_poll()
        payload = _read_status(self.directory)
        entry = _entry(payload, "brush")
        self.assertEqual(entry["state"], "loaded")
        self.assertEqual(entry["kind"], "persistent")
        self.assertTrue(entry["active"])
        self.assertFalse(entry["last_good"])
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["file_hash"], _sha256(path))
        self.assertEqual(entry["active_hash"], _sha256(path))
        self.assertFalse(error_file(self.directory, path).exists())

    def test_failed_load_writes_traceback_an_agent_can_use(self):
        path = self.directory / "broken.py"
        path.write_text(SYNTAX_ERROR, encoding="utf-8")
        self._stable_poll()
        payload = _read_status(self.directory)
        entry = _entry(payload, "broken")
        self.assertEqual(entry["state"], "error")
        self.assertFalse(entry["active"])
        self.assertFalse(entry["last_good"])
        self.assertEqual(entry["file_hash"], _sha256(path))
        self.assertIsNone(entry["active_hash"])
        self.assertEqual(entry["error"]["type"], "SyntaxError")
        self.assertIn("SyntaxError", entry["error"]["traceback"])
        self.assertIn(str(path), entry["error"]["traceback"])
        sidecar = error_file(self.directory, path)
        self.assertTrue(sidecar.is_file())
        self.assertIn("SyntaxError", sidecar.read_text(encoding="utf-8"))
        self.assertIn(str(path), sidecar.read_text(encoding="utf-8"))

    def test_status_distinguishes_active_last_good_from_failed_candidate(self):
        path = self.directory / "brush.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        good_hash = _sha256(path)
        path.write_text(SYNTAX_ERROR, encoding="utf-8")
        self._stable_poll()
        payload = _read_status(self.directory)
        entry = _entry(payload, "brush")
        self.assertEqual(entry["state"], "error")
        self.assertTrue(entry["active"])
        self.assertTrue(entry["last_good"])
        self.assertEqual(entry["file_hash"], _sha256(path))
        self.assertEqual(entry["active_hash"], good_hash)
        self.assertNotEqual(entry["file_hash"], entry["active_hash"])
        self.assertEqual(entry["error"]["type"], "SyntaxError")
        self.assertIn("SyntaxError", error_file(self.directory, path).read_text(encoding="utf-8"))
        record = self.host.loaded_blendlet(path)
        self.assertTrue(record.persistent)
        self.assertIsInstance(record.error, SyntaxError)

    def test_successful_reload_clears_the_error_sidecar(self):
        path = self.directory / "brush.py"
        path.write_text(SYNTAX_ERROR, encoding="utf-8")
        self._stable_poll()
        self.assertTrue(error_file(self.directory, path).is_file())
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        payload = _read_status(self.directory)
        entry = _entry(payload, "brush")
        self.assertEqual(entry["state"], "loaded")
        self.assertIsNone(entry["error"])
        self.assertFalse(error_file(self.directory, path).exists())

    def test_transient_success_is_ran_not_loaded(self):
        path = self.directory / "once.py"
        path.write_text(TRANSIENT, encoding="utf-8")
        self._stable_poll()
        entry = _entry(_read_status(self.directory), "once")
        self.assertEqual(entry["state"], "ran")
        self.assertEqual(entry["kind"], "transient")
        self.assertFalse(entry["active"])
        self.assertFalse(entry["last_good"])
        self.assertEqual(entry["file_hash"], _sha256(path))
        self.assertEqual((path.with_suffix(".out")).read_text(encoding="utf-8"), "ran")

    def test_stop_marks_the_host_not_running(self):
        path = self.directory / "brush.py"
        path.write_text(PERSISTENT, encoding="utf-8")
        self._stable_poll()
        self.host.stop()
        payload = _read_status(self.directory)
        self.assertFalse(payload["host"]["running"])
        self.assertIsNone(payload["host"]["blendlets_directory"])
        self.assertEqual(payload["blendlets"], [])

    def test_identical_status_is_not_rewritten(self):
        path = status_file(self.directory)
        first_mtime = path.stat().st_mtime_ns
        first_text = path.read_text(encoding="utf-8")
        self.host.poll()
        self.assertEqual(path.read_text(encoding="utf-8"), first_text)
        self.assertEqual(path.stat().st_mtime_ns, first_mtime)


class StatusWriteSkipTests(unittest.TestCase):
    def test_second_write_with_same_payload_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            now = datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc)
            path, signature, written = write_status(
                directory,
                running=True,
                now=now,
            )
            self.assertTrue(written)
            self.assertTrue(path.is_file())
            later = datetime(2026, 9, 19, 6, 1, tzinfo=timezone.utc)
            _path, same, rewritten = write_status(
                directory,
                running=True,
                now=later,
                previous_signature=signature,
            )
            self.assertFalse(rewritten)
            self.assertEqual(same, signature)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["updated_at"],
                "2026-09-19T06:00:00Z",
            )


class LifecycleHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()
        self.lifecycle = Lifecycle()

    def tearDown(self) -> None:
        self.lifecycle.unload_all()
        self.tmpdir.cleanup()

    def test_failed_register_keeps_attempted_hash_without_active_hash(self):
        path = self.directory / "bad.py"
        path.write_text(RAISING_REGISTER, encoding="utf-8")
        record = self.lifecycle.load(path)
        self.assertEqual(record.attempted_hash, fingerprint_file(path))
        self.assertIsNone(record.source_hash)
        self.assertIsInstance(record.error, RuntimeError)
