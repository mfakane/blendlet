"""Load, reload, and unload blendlet Python modules.

Execution contract:

* ``register(ctx)`` — persistent tool; stays loaded until reload/unload
* optional ``unregister(ctx)`` — explicit cleanup before unload
* ``run(ctx)`` without ``register`` — transient one-shot; the module is dropped
  after ``run`` so it does not become a permanent add-on
* ordinary ``bpy`` remains available; there is no custom operator DSL
* resources registered through ``ctx`` are disposed automatically; a simple
  blendlet does not need ``unregister()`` merely to undo those resources

Reloads stage a candidate module before replacing the active one. A
syntax/import failure keeps the last working persistent tool. After the old
version is disposed, a failed ``register()``/``run()`` restores that last-good
tool when restoration itself succeeds. Python objects are not carried from
the candidate onto the last-good module.
"""

from __future__ import annotations

import hashlib
import sys
import traceback
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import Context
from .discovery import ChangeKind, FileChange


def module_name_for(path: Path, generation: int = 0) -> str:
    """Unique ``sys.modules`` key for a blendlet path and import generation."""
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    if generation:
        return f"_blendlet_mod_{digest}_{generation}"
    return f"_blendlet_mod_{digest}"


def _format_error(error: BaseException, restore: BaseException | None = None) -> str:
    parts = ["".join(traceback.format_exception(error)).rstrip()]
    if restore is not None:
        parts.append("Last-good restore also failed:")
        parts.append("".join(traceback.format_exception(restore)).rstrip())
    return "\n".join(parts)


@dataclass
class Blendlet:
    path: Path
    context: Context
    module_name: str | None = None
    module: types.ModuleType | None = None
    persistent: bool = False
    error: BaseException | None = None
    error_traceback: str | None = None


def _drop_module(name: str | None) -> None:
    if name:
        sys.modules.pop(name, None)


def _dispose(context: Context) -> None:
    try:
        context.dispose()
    except Exception:
        pass


def _set_error(
    record: Blendlet,
    error: BaseException,
    restore: BaseException | None = None,
) -> None:
    record.error = error
    record.error_traceback = _format_error(error, restore)


def _clear_error(record: Blendlet) -> None:
    record.error = None
    record.error_traceback = None


def import_blendlet(path: Path, *, name: str | None = None) -> tuple[str, types.ModuleType]:
    """Import a blendlet file under a unique module name.

    Source is compiled on each load so a rewrite is not masked by leftover
    ``.pyc`` files when mtimes do not change. ``name`` must not collide with a
    last-good module still held in ``sys.modules``.
    """
    path = Path(path)
    if name is None:
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

    def __init__(self, *, api: Any = None) -> None:
        self._loaded: dict[Path, Blendlet] = {}
        self._generations: dict[Path, int] = {}
        self._api = api

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
            return self.reload(path)
        return self._load_fresh(path)

    def reload(self, path: Path) -> Blendlet:
        path = Path(path)
        current = self._loaded.get(path)
        if current is not None and current.persistent and current.module is not None:
            return self._reload_transactional(current)
        if current is not None:
            self.unload(path)
        return self._load_fresh(path)

    def unload(self, path: Path) -> None:
        record = self._loaded.pop(Path(path), None)
        if record is None:
            return
        self._teardown(record, drop_module=True)

    def unload_all(self) -> None:
        for path in list(self._loaded):
            self.unload(path)

    def _next_name(self, path: Path) -> str:
        generation = self._generations.get(path, 0)
        self._generations[path] = generation + 1
        return module_name_for(path, generation)

    def _import(self, path: Path) -> tuple[str, types.ModuleType]:
        return import_blendlet(path, name=self._next_name(path))

    def _load_fresh(self, path: Path) -> Blendlet:
        context = Context(path, api=self._api)
        record = Blendlet(path=path, context=context)
        self._loaded[path] = record
        try:
            name, module = self._import(path)
        except Exception as exc:
            _set_error(record, exc)
            return record
        try:
            self._activate(record, name, module)
        except Exception:
            self._abandon_candidate(record, name)
        return record

    def _reload_transactional(self, current: Blendlet) -> Blendlet:
        last_good_name = current.module_name
        last_good_module = current.module
        try:
            name, module = self._import(current.path)
        except Exception as exc:
            _set_error(current, exc)
            return current

        self._teardown(current, drop_module=False)
        candidate = Blendlet(path=current.path, context=Context(current.path, api=self._api))
        self._loaded[current.path] = candidate
        try:
            self._activate(candidate, name, module)
        except Exception as exc:
            self._abandon_candidate(candidate, name)
            restored = self._restore_last_good(
                current,
                module=last_good_module,
                module_name=last_good_name,
                error=exc,
            )
            self._loaded[current.path] = restored
            return restored
        _drop_module(last_good_name)
        return candidate

    def _activate(self, record: Blendlet, name: str, module: types.ModuleType) -> None:
        record.module_name = name
        record.module = module
        record.persistent = False
        register = getattr(module, "register", None)
        run = getattr(module, "run", None)
        try:
            if callable(register):
                register(record.context)
                record.persistent = True
            elif callable(run):
                run(record.context)
        except Exception as exc:
            record.persistent = False
            _set_error(record, exc)
            raise
        _clear_error(record)
        if not record.persistent:
            _dispose(record.context)
            if callable(register) or callable(run):
                _drop_module(name)
                record.module = None
                record.module_name = None

    def _abandon_candidate(self, record: Blendlet, name: str) -> None:
        _dispose(record.context)
        _drop_module(name)
        record.module = None
        record.module_name = None
        record.persistent = False

    def _restore_last_good(
        self,
        last_good: Blendlet,
        *,
        module: types.ModuleType | None,
        module_name: str | None,
        error: BaseException,
    ) -> Blendlet:
        last_good.context = Context(last_good.path, api=self._api)
        last_good.module = module
        last_good.module_name = module_name
        last_good.persistent = False
        register = getattr(module, "register", None) if module is not None else None
        try:
            if callable(register):
                register(last_good.context)
                last_good.persistent = True
        except Exception as restore_exc:
            _dispose(last_good.context)
            _drop_module(module_name)
            last_good.module = None
            last_good.module_name = None
            last_good.persistent = False
            _set_error(last_good, error, restore=restore_exc)
            return last_good
        _set_error(last_good, error)
        return last_good

    def _teardown(self, record: Blendlet, *, drop_module: bool) -> None:
        try:
            module = record.module
            if module is not None:
                unregister = getattr(module, "unregister", None)
                if callable(unregister):
                    try:
                        unregister(record.context)
                    except Exception:
                        pass
        finally:
            _dispose(record.context)
            if drop_module:
                _drop_module(record.module_name)
                record.module = None
                record.module_name = None
                record.persistent = False
