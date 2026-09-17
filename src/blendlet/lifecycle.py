"""Load, reload, and unload blendlet Python modules.

Execution contract:

* ``register(ctx)`` — persistent tool; stays loaded until reload/unload
* optional ``unregister(ctx)`` — explicit cleanup before unload
* ``run(ctx)`` without ``register`` — transient one-shot; the module is dropped
  after ``run`` so it does not become a permanent add-on
* ordinary ``bpy`` remains available; there is no custom operator DSL

Resource tracking on ``ctx`` is a later ticket. Failed loads are recorded and
do not stop the host; keeping a last-good version across a failed reload is
also later work.
"""

from __future__ import annotations

import hashlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from .discovery import ChangeKind, FileChange


def module_name_for(path: Path) -> str:
    """Stable unique ``sys.modules`` key for a blendlet path."""
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"_blendlet_mod_{digest}"


class Context:
    """Per-blendlet execution context passed to register/unregister/run."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.name = self.path.stem


@dataclass
class Blendlet:
    path: Path
    context: Context
    module_name: str | None = None
    module: types.ModuleType | None = None
    persistent: bool = False
    error: BaseException | None = None


def _drop_module(name: str | None) -> None:
    if name:
        sys.modules.pop(name, None)


def import_blendlet(path: Path) -> tuple[str, types.ModuleType]:
    """Import a blendlet file under a unique module name.

    Source is compiled on each load so a rewrite is not masked by leftover
    ``.pyc`` files when mtimes do not change.
    """
    path = Path(path)
    name = module_name_for(path)
    source = path.read_text(encoding="utf-8")
    code = compile(source, str(path), "exec")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return name, module


class Lifecycle:
    """Apply file events to imported blendlet modules."""

    def __init__(self) -> None:
        self._loaded: dict[Path, Blendlet] = {}

    def get(self, path: Path) -> Blendlet | None:
        return self._loaded.get(Path(path))

    def blendlets(self) -> tuple[Blendlet, ...]:
        return tuple(
            self._loaded[path]
            for path in sorted(self._loaded, key=lambda item: item.as_posix())
        )

    def persistent_files(self) -> tuple[Path, ...]:
        return tuple(
            record.path
            for record in self.blendlets()
            if record.persistent and record.module is not None
        )

    def handle(self, change: FileChange) -> None:
        if change.kind is ChangeKind.CREATED:
            self.load(change.path)
        elif change.kind is ChangeKind.MODIFIED:
            self.reload(change.path)
        elif change.kind is ChangeKind.DELETED:
            self.unload(change.path)

    def load(self, path: Path) -> Blendlet:
        path = Path(path)
        if path in self._loaded:
            self.unload(path)
        context = Context(path)
        record = Blendlet(path=path, context=context)
        self._loaded[path] = record
        try:
            name, module = import_blendlet(path)
        except Exception as exc:
            record.error = exc
            return record

        record.module_name = name
        record.module = module
        register = getattr(module, "register", None)
        run = getattr(module, "run", None)
        try:
            if callable(register):
                register(context)
                record.persistent = True
            elif callable(run):
                run(context)
                _drop_module(name)
                record.module = None
                record.module_name = None
        except Exception as exc:
            record.error = exc
            record.persistent = False
            _drop_module(name)
            record.module = None
            record.module_name = None
        return record

    def reload(self, path: Path) -> Blendlet:
        self.unload(path)
        return self.load(path)

    def unload(self, path: Path) -> None:
        record = self._loaded.pop(Path(path), None)
        if record is None:
            return
        module = record.module
        if module is not None:
            unregister = getattr(module, "unregister", None)
            if callable(unregister):
                try:
                    unregister(record.context)
                except Exception:
                    pass
        _drop_module(record.module_name)

    def unload_all(self) -> None:
        for path in list(self._loaded):
            self.unload(path)
