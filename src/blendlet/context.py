"""Per-blendlet execution context with owned Blender resource cleanup.

Blendlets register classes, keymaps, draw handlers, timers, and other
host-supported callbacks through ``ctx``. ``dispose()`` undoes those
registrations, so a simple tool does not need ``unregister()`` merely to
tear down resources it registered through the context.

``Lifecycle`` calls ``dispose()`` after optional ``unregister(ctx)``.
``dispose()`` is idempotent, so ``unregister`` may also call it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _Owned:
    kind: str
    dispose: Callable[[], None]
    key: object = None
    disposed: bool = False

    def run(self) -> None:
        if self.disposed:
            return
        self.disposed = True
        self.dispose()


@dataclass
class Context:
    """Isolated per-blendlet context passed to register/unregister/run."""

    path: Path
    api: Any = field(default=None, repr=False, compare=False)
    name: str = field(init=False)
    _owned: list[_Owned] = field(default_factory=list, init=False, repr=False)
    _disposed: bool = field(default=False, init=False, repr=False)
    _msgbus_owner: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.name = self.path.stem

    @property
    def disposed(self) -> bool:
        return self._disposed

    def register_class(self, cls: type) -> type:
        """Register a ``bpy`` class and take ownership of it."""
        self._require_active()
        self._blender().utils.register_class(cls)
        blender = self._blender()

        def dispose(cls: type = cls, blender: Any = blender) -> None:
            if not getattr(cls, "is_registered", True):
                return
            blender.utils.unregister_class(cls)

        self._own("class", dispose, key=cls)
        return cls

    def unregister_class(self, cls: type) -> None:
        """Unregister a class now. Later ``dispose()`` will skip it."""
        owned = self._find("class", cls)
        if owned is not None and owned.disposed:
            return
        self._blender().utils.unregister_class(cls)
        if owned is not None:
            owned.disposed = True

    def add_timer(
        self,
        function: Callable[..., float | None],
        *,
        first_interval: float = 0.0,
        persistent: bool = False,
    ) -> Callable[..., float | None]:
        """Register a ``bpy.app.timers`` callback and take ownership of it."""
        self._require_active()
        timers = self._blender().app.timers
        timers.register(function, first_interval=first_interval, persistent=persistent)

        def dispose(
            function: Callable[..., float | None] = function,
            timers: Any = timers,
        ) -> None:
            if not timers.is_registered(function):
                return
            timers.unregister(function)

        self._own("timer", dispose, key=function)
        return function

    def remove_timer(self, function: Callable[..., float | None]) -> None:
        owned = self._find("timer", function)
        if owned is not None and owned.disposed:
            return
        timers = self._blender().app.timers
        if timers.is_registered(function):
            timers.unregister(function)
        if owned is not None:
            owned.disposed = True

    def add_draw_handler(
        self,
        space: Any,
        callback: Callable[..., None],
        args: tuple[Any, ...] = (),
        *,
        region_type: str = "WINDOW",
        draw_type: str = "POST_PIXEL",
    ) -> Any:
        """Add a space draw handler and take ownership of the handle."""
        self._require_active()
        handle = space.draw_handler_add(callback, args, region_type, draw_type)

        def dispose(
            space: Any = space,
            handle: Any = handle,
            region_type: str = region_type,
        ) -> None:
            space.draw_handler_remove(handle, region_type)

        self._own("draw_handler", dispose, key=handle)
        return handle

    def remove_draw_handler(self, handle: Any) -> None:
        owned = self._find("draw_handler", handle)
        if owned is None or owned.disposed:
            raise KeyError("draw handler is not owned by this context")
        owned.run()

    def add_keymap(
        self,
        name: str = "Window",
        *,
        space_type: str = "EMPTY",
        region_type: str = "WINDOW",
    ) -> Any:
        """Create an add-on keymap and take ownership of it."""
        self._require_active()
        blender = self._blender()
        wm = blender.context.window_manager
        if wm is None:
            raise RuntimeError("window manager is unavailable")
        keyconfigs = wm.keyconfigs
        kc = keyconfigs.addon
        if kc is None:
            raise RuntimeError("addon keyconfig is unavailable")
        km = kc.keymaps.new(name=name, space_type=space_type, region_type=region_type)

        def dispose(kc: Any = kc, km: Any = km) -> None:
            kc.keymaps.remove(km)

        self._own("keymap", dispose, key=km)
        return km

    def add_keymap_item(self, keymap: Any, idname: str, *, type: str, value: str = "PRESS", **kwargs: Any) -> Any:
        """Add a keymap item and take ownership of it."""
        self._require_active()
        item = keymap.keymap_items.new(idname, type=type, value=value, **kwargs)

        def dispose(keymap: Any = keymap, item: Any = item) -> None:
            keymap.keymap_items.remove(item)

        self._own("keymap_item", dispose, key=item)
        return item

    def remove_keymap_item(self, item: Any) -> None:
        owned = self._find("keymap_item", item)
        if owned is None or owned.disposed:
            raise KeyError("keymap item is not owned by this context")
        owned.run()

    def remove_keymap(self, keymap: Any) -> None:
        owned = self._find("keymap", keymap)
        if owned is None or owned.disposed:
            raise KeyError("keymap is not owned by this context")
        owned.run()

    def add_handler(self, handlers: Any, callback: Callable[..., Any]) -> Callable[..., Any]:
        """Append a callback to a ``bpy.app.handlers`` list and own it."""
        self._require_active()
        handlers.append(callback)

        def dispose(handlers: Any = handlers, callback: Callable[..., Any] = callback) -> None:
            try:
                handlers.remove(callback)
            except ValueError:
                return

        self._own("handler", dispose, key=(id(handlers), callback))
        return callback

    def remove_handler(self, handlers: Any, callback: Callable[..., Any]) -> None:
        owned = self._find("handler", (id(handlers), callback))
        if owned is not None and owned.disposed:
            return
        try:
            handlers.remove(callback)
        except ValueError:
            pass
        if owned is not None:
            owned.disposed = True

    def subscribe_rna(self, key: Any, notify: Callable[..., Any], args: tuple[Any, ...] = ()) -> None:
        """Subscribe to an RNA msgbus notification owned by this context."""
        self._require_active()
        blender = self._blender()
        if self._msgbus_owner is None:
            self._msgbus_owner = object()
            owner = self._msgbus_owner

            def dispose(blender: Any = blender, owner: object = owner) -> None:
                blender.msgbus.clear_by_owner(owner)

            self._own("msgbus", dispose, key=owner)
        blender.msgbus.subscribe_rna(
            key=key,
            owner=self._msgbus_owner,
            args=args,
            notify=notify,
        )

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` during ``dispose()`` (LIFO with other resources)."""
        self._require_active()
        self._own("cleanup", callback, key=callback)

    def dispose(self) -> None:
        """Release owned resources. Safe to call more than once."""
        if self._disposed:
            return
        self._disposed = True
        while self._owned:
            owned = self._owned.pop()
            try:
                owned.run()
            except Exception:
                pass

    def _blender(self) -> Any:
        if self.api is None:
            import bpy

            self.api = bpy
        return self.api

    def _require_active(self) -> None:
        if self._disposed:
            raise RuntimeError("context is disposed")

    def _own(self, kind: str, dispose: Callable[[], None], key: object = None) -> None:
        self._owned.append(_Owned(kind=kind, dispose=dispose, key=(kind, key)))

    def _find(self, kind: str, key: object) -> _Owned | None:
        needle = (kind, key)
        for owned in reversed(self._owned):
            if owned.key == needle:
                return owned
        return None
