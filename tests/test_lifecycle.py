from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from blendlet.discovery import ChangeKind, FileChange
from blendlet.lifecycle import Lifecycle, module_name_for


PERSISTENT = """\
from pathlib import Path

LOG = Path(__file__).with_suffix(".log")


def _append(line: str) -> None:
    previous = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    LOG.write_text(previous + line + "\\n", encoding="utf-8")


def register(ctx):
    _append(f"register:{{ctx.name}}:{{VALUE}}")


def unregister(ctx):
    _append(f"unregister:{{ctx.name}}")


VALUE = {value}
"""

TRANSIENT = """\
from pathlib import Path

OUT = Path(__file__).with_suffix(".out")
previous = int(OUT.read_text(encoding="utf-8")) if OUT.exists() else 0


def run(ctx):
    OUT.write_text(str(previous + {n}), encoding="utf-8")
"""

BOTH = """\
from pathlib import Path

LOG = Path(__file__).with_suffix(".log")


def register(ctx):
    LOG.write_text("register\\n", encoding="utf-8")


def run(ctx):
    LOG.write_text("run\\n", encoding="utf-8")
"""

SYNTAX_ERROR = "def register(ctx):\n    $$$\n"

RAISING_REGISTER = """\
def register(ctx):
    raise RuntimeError("boom")
"""


def _modules_for(path: Path) -> list[str]:
    found: list[str] = []
    resolved = path.resolve()
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        file = getattr(module, "__file__", None)
        if not file:
            continue
        try:
            if Path(file).resolve() == resolved:
                found.append(name)
        except OSError:
            continue
    return found


def _log_lines(path: Path) -> list[str]:
    log = path.with_suffix(".log")
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()
        self.lifecycle = Lifecycle()

    def tearDown(self) -> None:
        self.lifecycle.unload_all()
        self.tmpdir.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_register_loads_a_persistent_tool_and_passes_ctx(self):
        path = self._write("brush.py", PERSISTENT.format(value=1))
        record = self.lifecycle.load(path)
        self.assertTrue(record.persistent)
        self.assertIsNone(record.error)
        self.assertEqual(record.context.path, path)
        self.assertEqual(record.context.name, "brush")
        self.assertEqual(_log_lines(path), ["register:brush:1"])
        self.assertEqual(self.lifecycle.persistent_files(), (path,))
        self.assertIn(record.module_name, sys.modules)
        self.assertEqual(_modules_for(path), [record.module_name])

    def test_modified_persistent_file_is_unregistered_then_reimported(self):
        path = self._write("brush.py", PERSISTENT.format(value=1))
        first = self.lifecycle.load(path)
        path.write_text(PERSISTENT.format(value=7), encoding="utf-8")
        second = self.lifecycle.reload(path)
        self.assertEqual(
            _log_lines(path),
            ["register:brush:1", "unregister:brush", "register:brush:7"],
        )
        self.assertEqual(second.module.VALUE, 7)
        self.assertIsNot(first.module, second.module)
        self.assertIs(sys.modules[second.module_name], second.module)

    def test_deleted_persistent_file_unregisters_and_drops_the_module(self):
        path = self._write("brush.py", PERSISTENT.format(value=1))
        record = self.lifecycle.load(path)
        name = record.module_name
        self.lifecycle.unload(path)
        self.assertEqual(_log_lines(path), ["register:brush:1", "unregister:brush"])
        self.assertIsNone(self.lifecycle.get(path))
        self.assertEqual(self.lifecycle.persistent_files(), ())
        self.assertNotIn(name, sys.modules)
        self.assertEqual(_modules_for(path), [])

    def test_run_executes_once_and_does_not_stay_loaded(self):
        path = self._write("once.py", TRANSIENT.format(n=1))
        record = self.lifecycle.load(path)
        self.assertFalse(record.persistent)
        self.assertIsNone(record.module)
        self.assertIsNone(record.module_name)
        self.assertIsNone(record.error)
        self.assertEqual((path.with_suffix(".out")).read_text(encoding="utf-8"), "1")
        self.assertEqual(self.lifecycle.persistent_files(), ())
        self.assertEqual(_modules_for(path), [])
        self.assertNotIn(module_name_for(path), sys.modules)

    def test_modified_transient_script_runs_again_without_becoming_persistent(self):
        path = self._write("once.py", TRANSIENT.format(n=1))
        self.lifecycle.load(path)
        path.write_text(TRANSIENT.format(n=1), encoding="utf-8")
        record = self.lifecycle.reload(path)
        self.assertFalse(record.persistent)
        self.assertEqual((path.with_suffix(".out")).read_text(encoding="utf-8"), "2")
        self.assertEqual(_modules_for(path), [])

    def test_register_wins_over_run(self):
        path = self._write("both.py", BOTH)
        record = self.lifecycle.load(path)
        self.assertTrue(record.persistent)
        self.assertEqual(_log_lines(path), ["register"])

    def test_file_events_drive_load_reload_unload(self):
        path = self._write("brush.py", PERSISTENT.format(value=1))
        self.lifecycle.handle(FileChange(ChangeKind.CREATED, path))
        self.assertEqual(_log_lines(path), ["register:brush:1"])
        path.write_text(PERSISTENT.format(value=2), encoding="utf-8")
        self.lifecycle.handle(FileChange(ChangeKind.MODIFIED, path))
        self.assertEqual(
            _log_lines(path),
            ["register:brush:1", "unregister:brush", "register:brush:2"],
        )
        self.lifecycle.handle(FileChange(ChangeKind.DELETED, path))
        self.assertEqual(
            _log_lines(path),
            [
                "register:brush:1",
                "unregister:brush",
                "register:brush:2",
                "unregister:brush",
            ],
        )
        self.assertEqual(_modules_for(path), [])

    def test_syntax_error_is_recorded_and_does_not_block_other_files(self):
        broken = self._write("broken.py", SYNTAX_ERROR)
        good = self._write("good.py", PERSISTENT.format(value=1))
        failed = self.lifecycle.load(broken)
        loaded = self.lifecycle.load(good)
        self.assertIsInstance(failed.error, SyntaxError)
        self.assertFalse(failed.persistent)
        self.assertIsNone(loaded.error)
        self.assertTrue(loaded.persistent)
        self.assertEqual(self.lifecycle.persistent_files(), (good,))
        self.assertEqual(_modules_for(broken), [])

    def test_failed_register_drops_the_module(self):
        path = self._write("bad.py", RAISING_REGISTER)
        record = self.lifecycle.load(path)
        self.assertIsInstance(record.error, RuntimeError)
        self.assertEqual(str(record.error), "boom")
        self.assertFalse(record.persistent)
        self.assertEqual(_modules_for(path), [])
        self.assertNotIn(module_name_for(path), sys.modules)

    def test_unload_all_unregisters_persistent_tools(self):
        first = self._write("a.py", PERSISTENT.format(value=1))
        second = self._write("b.py", PERSISTENT.format(value=1))
        self.lifecycle.load(first)
        self.lifecycle.load(second)
        self.lifecycle.unload_all()
        self.assertEqual(_log_lines(first)[-1], "unregister:a")
        self.assertEqual(_log_lines(second)[-1], "unregister:b")
        self.assertEqual(self.lifecycle.persistent_files(), ())
        self.assertEqual(_modules_for(first), [])
        self.assertEqual(_modules_for(second), [])

    def test_optional_unregister_is_skipped_when_absent(self):
        path = self._write(
            "plain.py",
            "def register(ctx):\n    ctx.seen = True\n",
        )
        record = self.lifecycle.load(path)
        self.assertTrue(record.context.seen)
        self.lifecycle.unload(path)
        self.assertEqual(_modules_for(path), [])
