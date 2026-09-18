from __future__ import annotations

import unittest
from pathlib import Path

from blendlet.context import Context

from fakes import FakeBpy


class Probe:
    bl_idname = "blendlet.probe"


class Other:
    bl_idname = "blendlet.other"


def _tick() -> float:
    return 1.0


def _draw(_context) -> None:
    return None


class ContextOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bpy = FakeBpy()
        self.ctx = Context(Path("brush.py"), api=self.bpy)

    def test_register_class_is_released_by_dispose(self):
        self.ctx.register_class(Probe)
        self.assertEqual(self.bpy.utils.classes, [Probe])
        self.ctx.dispose()
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertTrue(self.ctx.disposed)

    def test_dispose_is_idempotent(self):
        self.ctx.register_class(Probe)
        self.ctx.add_timer(_tick, persistent=True)
        self.ctx.dispose()
        self.ctx.dispose()
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertEqual(self.bpy.app.timers.registered, [])

    def test_unregister_class_coexists_with_later_dispose(self):
        self.ctx.register_class(Probe)
        self.ctx.add_timer(_tick)
        self.ctx.unregister_class(Probe)
        self.assertEqual(self.bpy.utils.classes, [])
        self.ctx.dispose()
        self.assertEqual(self.bpy.utils.classes, [])
        self.assertEqual(self.bpy.app.timers.registered, [])

    def test_manual_dispose_then_automatic_dispose_does_not_double_unregister(self):
        self.ctx.register_class(Probe)
        self.ctx.dispose()
        self.ctx.dispose()
        self.assertEqual(self.bpy.utils.classes, [])

    def test_contexts_are_isolated(self):
        other = Context(Path("other.py"), api=self.bpy)
        self.ctx.register_class(Probe)
        other.register_class(Other)
        self.ctx.dispose()
        self.assertEqual(self.bpy.utils.classes, [Other])
        other.dispose()
        self.assertEqual(self.bpy.utils.classes, [])

    def test_default_state_is_isolated_and_survives_dispose(self):
        other = Context(Path("other.py"), api=self.bpy)
        self.ctx.state["radius"] = 4.0
        self.assertNotIn("radius", other.state)
        self.ctx.dispose()
        self.assertEqual(self.ctx.state["radius"], 4.0)

    def test_injected_state_is_the_same_mapping(self):
        slot = {"radius": 8.0}
        ctx = Context(Path("brush.py"), api=self.bpy, state=slot)
        ctx.state["radius"] = 12.0
        self.assertEqual(slot["radius"], 12.0)
        self.assertIs(ctx.state, slot)

    def test_timer_draw_handler_keymap_and_app_handler_are_owned(self):
        handle = self.ctx.add_draw_handler(self.bpy.types.SpaceView3D, _draw, ())
        self.ctx.add_timer(_tick, first_interval=0.1, persistent=True)
        keymap = self.ctx.add_keymap("3D View", space_type="VIEW_3D")
        item = self.ctx.add_keymap_item(keymap, "mesh.knife_tool", type="K")
        self.ctx.add_handler(self.bpy.app.handlers.depsgraph_update_post, _tick)
        self.ctx.subscribe_rna({"foo": 1}, _tick)

        self.assertEqual(len(self.bpy.types.SpaceView3D.handlers), 1)
        self.assertEqual(self.bpy.types.SpaceView3D.handlers[0][0], handle)
        self.assertEqual(self.bpy.app.timers.registered, [_tick])
        self.assertEqual(self.bpy.context.window_manager.keyconfigs.addon.keymaps.maps, [keymap])
        self.assertEqual(keymap.keymap_items.items, [item])
        self.assertEqual(self.bpy.app.handlers.depsgraph_update_post, [_tick])
        self.assertEqual(len(self.bpy.msgbus.subscriptions), 1)

        self.ctx.dispose()
        self.assertEqual(self.bpy.types.SpaceView3D.handlers, [])
        self.assertEqual(self.bpy.app.timers.registered, [])
        self.assertEqual(self.bpy.context.window_manager.keyconfigs.addon.keymaps.maps, [])
        self.assertEqual(self.bpy.app.handlers.depsgraph_update_post, [])
        self.assertEqual(self.bpy.msgbus.subscriptions, [])

    def test_early_remove_skips_that_resource_on_dispose(self):
        handle = self.ctx.add_draw_handler(self.bpy.types.SpaceView3D, _draw, ())
        keymap = self.ctx.add_keymap()
        item = self.ctx.add_keymap_item(keymap, "wm.call_menu", type="A")
        self.ctx.add_timer(_tick)
        self.ctx.add_handler(self.bpy.app.handlers.frame_change_pre, _tick)

        self.ctx.remove_draw_handler(handle)
        self.ctx.remove_keymap_item(item)
        self.ctx.remove_keymap(keymap)
        self.ctx.remove_timer(_tick)
        self.ctx.remove_handler(self.bpy.app.handlers.frame_change_pre, _tick)

        self.assertEqual(self.bpy.types.SpaceView3D.handlers, [])
        self.assertEqual(self.bpy.app.timers.registered, [])
        self.assertEqual(self.bpy.context.window_manager.keyconfigs.addon.keymaps.maps, [])
        self.assertEqual(self.bpy.app.handlers.frame_change_pre, [])
        self.ctx.dispose()
        self.assertEqual(self.bpy.types.SpaceView3D.handlers, [])

    def test_cleanup_runs_lifo_and_survives_a_failing_owner(self):
        order: list[str] = []
        self.ctx.register_class(Probe)

        def boom() -> None:
            order.append("boom")
            raise RuntimeError("cleanup failed")

        self.ctx.add_cleanup(boom)
        self.ctx.add_cleanup(lambda: order.append("last"))
        self.ctx.dispose()
        self.assertEqual(order, ["last", "boom"])
        self.assertEqual(self.bpy.utils.classes, [])

    def test_registration_after_dispose_is_rejected(self):
        self.ctx.dispose()
        with self.assertRaises(RuntimeError):
            self.ctx.register_class(Probe)
        with self.assertRaises(RuntimeError):
            self.ctx.add_cleanup(lambda: None)
        self.assertEqual(self.bpy.utils.classes, [])
