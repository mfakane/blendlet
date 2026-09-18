"""In-process stand-ins for the bpy surfaces Context owns."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeTimers:
    def __init__(self) -> None:
        self.registered: list[Any] = []

    def register(self, function, first_interval: float = 0.0, persistent: bool = False) -> None:
        if function in self.registered:
            raise ValueError("timer already registered")
        self.registered.append(function)

    def unregister(self, function) -> None:
        self.registered.remove(function)

    def is_registered(self, function) -> bool:
        return function in self.registered


class FakeUtils:
    def __init__(self) -> None:
        self.classes: list[type] = []

    def register_class(self, cls: type) -> None:
        if cls in self.classes:
            raise ValueError(f"{cls} already registered")
        bl_idname = getattr(cls, "bl_idname", None)
        if bl_idname is not None:
            for existing in self.classes:
                if getattr(existing, "bl_idname", None) == bl_idname:
                    raise ValueError(f"{bl_idname} already registered")
        self.classes.append(cls)

    def unregister_class(self, cls: type) -> None:
        self.classes.remove(cls)


class FakeSpace:
    def __init__(self) -> None:
        self.handlers: list[tuple[Any, Any, tuple[Any, ...], str, str]] = []

    def draw_handler_add(self, callback, args, region_type, draw_type):
        handle = object()
        self.handlers.append((handle, callback, args, region_type, draw_type))
        return handle

    def draw_handler_remove(self, handle, region_type) -> None:
        remaining = [item for item in self.handlers if item[0] is not handle]
        if len(remaining) == len(self.handlers):
            raise ValueError("draw handler is not registered")
        self.handlers = remaining


class FakeKeyMapItems:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def new(self, idname: str, **kwargs: Any) -> Any:
        item = SimpleNamespace(idname=idname, **kwargs)
        self.items.append(item)
        return item

    def remove(self, item: Any) -> None:
        self.items.remove(item)


class FakeKeyMap:
    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.keymap_items = FakeKeyMapItems()


class FakeKeyMaps:
    def __init__(self) -> None:
        self.maps: list[FakeKeyMap] = []

    def new(self, name: str, **kwargs: Any) -> FakeKeyMap:
        keymap = FakeKeyMap(name, **kwargs)
        self.maps.append(keymap)
        return keymap

    def remove(self, keymap: FakeKeyMap) -> None:
        self.maps.remove(keymap)


class FakeMsgBus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[Any, object, tuple[Any, ...], Any]] = []

    def subscribe_rna(self, key, owner, args, notify) -> None:
        self.subscriptions.append((key, owner, args, notify))

    def clear_by_owner(self, owner: object) -> None:
        self.subscriptions = [item for item in self.subscriptions if item[1] is not owner]


class FakeBpy:
    def __init__(self) -> None:
        self.utils = FakeUtils()
        self.app = SimpleNamespace(
            timers=FakeTimers(),
            handlers=SimpleNamespace(depsgraph_update_post=[], frame_change_pre=[]),
        )
        self.types = SimpleNamespace(SpaceView3D=FakeSpace())
        self.msgbus = FakeMsgBus()
        self.context = SimpleNamespace(
            window_manager=SimpleNamespace(
                keyconfigs=SimpleNamespace(addon=SimpleNamespace(keymaps=FakeKeyMaps()))
            )
        )
