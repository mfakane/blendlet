from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from blendlet.discovery import ChangeKind, FileChange
from blendlet.lifecycle import Lifecycle, module_name_for
from fakes import FakeBpy


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


OWNED_CLASS = """\
class Probe:
    bl_idname = "blendlet.probe"


def register(ctx):
    ctx.register_class(Probe)
"""

OWNED_CLASS_WITH_UNREGISTER = """\
from pathlib import Path

LOG = Path(__file__).with_suffix(".log")


def _append(line: str) -> None:
    previous = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    LOG.write_text(previous + line + "\\n", encoding="utf-8")


class Probe:
    bl_idname = "blendlet.probe"


def register(ctx):
    ctx.register_class(Probe)
    _append("register")


def unregister(ctx):
    ctx.unregister_class(Probe)
    _append("unregister")
"""

OWNED_THEN_RAISE = """\
class Probe:
    bl_idname = "blendlet.probe"


def register(ctx):
    ctx.register_class(Probe)
    raise RuntimeError("boom")
"""

OWNED_RUN = """\
class Probe:
    bl_idname = "blendlet.probe"


def run(ctx):
    ctx.register_class(Probe)
"""

DISPOSE_IN_UNREGISTER = """\
class Probe:
    bl_idname = "blendlet.probe"


def register(ctx):
    ctx.register_class(Probe)


def unregister(ctx):
    ctx.dispose()
"""


class ResourceOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmpdir.name).resolve()
        self.bpy = FakeBpy()
        self.lifecycle = Lifecycle(api=self.bpy)

    def tearDown(self) -> None:
        self.lifecycle.unload_all()
        self.tmpdir.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_owned_class_is_unregistered_without_an_unregister_hook(self):
        path = self._write("tool.py", OWNED_CLASS)
        record = self.lifecycle.load(path)
        self.assertTrue(record.persistent)
        self.assertEqual(self.bpy.utils.classes[0].bl_idname, "blendlet.probe")
        self.lifecycle.unload(path)
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertTrue(record.context.disposed)

    def test_reload_does_not_leave_duplicate_classes(self):
        path = self._write("tool.py", OWNED_CLASS)
        self.lifecycle.load(path)
        first = self.bpy.utils.classes[0]
        path.write_text(OWNED_CLASS + "\nMARKER = 1\n", encoding="utf-8")
        self.lifecycle.reload(path)
        self.assertEqual(len(self.bpy.utils.classes), 1)
        self.assertIsNot(self.bpy.utils.classes[0], first)
        self.assertEqual(self.bpy.utils.classes[0].bl_idname, "blendlet.probe")

    def test_two_blendlets_do_not_share_owned_resources(self):
        first = self._write(
            "a.py",
            "class Probe:\n    bl_idname = 'blendlet.a'\ndef register(ctx):\n    ctx.register_class(Probe)\n",
        )
        second = self._write(
            "b.py",
            "class Probe:\n    bl_idname = 'blendlet.b'\ndef register(ctx):\n    ctx.register_class(Probe)\n",
        )
        self.lifecycle.load(first)
        self.lifecycle.load(second)
        self.assertEqual(
            [cls.bl_idname for cls in self.bpy.utils.classes],
            ["blendlet.a", "blendlet.b"],
        )
        self.lifecycle.unload(first)
        self.assertEqual([cls.bl_idname for cls in self.bpy.utils.classes], ["blendlet.b"])
        self.lifecycle.unload(second)
        self.assertEqual(self.bpy.utils.classes, [])

    def test_manual_unregister_coexists_with_automatic_dispose(self):
        path = self._write("tool.py", OWNED_CLASS_WITH_UNREGISTER)
        self.lifecycle.load(path)
        self.lifecycle.unload(path)
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertEqual(_log_lines(path), ["register", "unregister"])

    def test_unregister_may_call_dispose_itself(self):
        path = self._write("tool.py", DISPOSE_IN_UNREGISTER)
        record = self.lifecycle.load(path)
        self.lifecycle.unload(path)
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertTrue(record.context.disposed)

    def test_failed_register_disposes_partial_resources(self):
        path = self._write("bad.py", OWNED_THEN_RAISE)
        record = self.lifecycle.load(path)
        self.assertIsInstance(record.error, RuntimeError)
        self.assertFalse(record.persistent)
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertTrue(record.context.disposed)

    def test_transient_run_disposes_owned_resources(self):
        path = self._write("once.py", OWNED_RUN)
        record = self.lifecycle.load(path)
        self.assertFalse(record.persistent)
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertTrue(record.context.disposed)
        self.assertEqual(_modules_for(path), [])

    def test_unload_all_disposes_owned_resources(self):
        path = self._write("tool.py", OWNED_CLASS)
        self.lifecycle.load(path)
        self.lifecycle.unload_all()
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertEqual(self.lifecycle.persistent_files(), ())

    def test_cleanup_without_unregister_runs_on_delete(self):
        path = self._write(
            "plain.py",
            "def register(ctx):\n"
            "    from pathlib import Path\n"
            "    log = Path(__file__).with_suffix('.log')\n"
            "    ctx.add_cleanup(lambda: log.write_text('disposed', encoding='utf-8'))\n",
        )
        self.lifecycle.load(path)
        self.assertFalse(path.with_suffix(".log").exists())
        self.lifecycle.handle(FileChange(ChangeKind.DELETED, path))
        self.assertEqual(path.with_suffix(".log").read_text(encoding="utf-8"), "disposed")
