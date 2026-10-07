"""Load the bundled examples through the host. Run under Blender, not CPython.

blender --background --factory-startup --python tests/blender_examples_harness.py

Background mode has no viewport to paint in, so the relax brush is checked
through its relaxation step on real Edit Mode mesh and UV data.
"""

from __future__ import annotations

import random
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
EXAMPLES = REPO_ROOT / "examples"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _fail(message: str) -> None:
    print(f"BLENDLET_EXAMPLES_FAIL: {message}", flush=True)
    raise SystemExit(1)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _signed_area(points) -> float:
    total = 0.0
    for k, a in enumerate(points):
        b = points[(k + 1) % len(points)]
        total += a.x * b.y - b.x * a.y
    return total * 0.5


def _tangle(target, points, *, plane):
    """Scramble interior vertices hard enough to fold quads over."""
    rng = random.Random(7)
    moved = {}
    for i, point in enumerate(points):
        if target.topology.is_boundary(i) or not target.topology.movable[i]:
            continue
        jittered = point.copy()
        jittered.x += rng.uniform(-1.0, 1.0) * plane
        jittered.y += rng.uniform(-1.0, 1.0) * plane
        moved[i] = jittered
    target.apply(moved)


def _relax_fully(module, target, steps: int = 400) -> None:
    for _ in range(steps):
        weights = {i: 1.0 for i, movable in enumerate(target.topology.movable) if movable}
        moved = module.relax_step(
            target.points(),
            target.topology,
            weights,
            strength=0.5,
            pin_boundary=True,
            constrain=target.constrain,
        )
        target.apply(moved)


def _check_relax_brush(record) -> None:
    import bmesh
    import bpy

    module = record.module
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2.0)
    obj = bpy.context.active_object
    original = [vertex.co.copy() for vertex in obj.data.vertices]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bm = bmesh.from_edit_mesh(obj.data)

    mesh = module.MeshTarget(obj, bm)
    _tangle(mesh, mesh.points(), plane=0.3)
    folded = sum(1 for face in bm.faces if face.normal.z < 0.0)
    _assert(folded > 0, "the tangled mesh should contain folded quads")
    _relax_fully(module, mesh)
    bm.normal_update()
    _assert(all(face.normal.z > 0.0 for face in bm.faces), "relaxing should unfold every quad")
    error = max((vert.co - original[vert.index]).length for vert in bm.verts)
    _assert(error < 1e-3, f"a relaxed grid should return to even squares, off by {error}")
    _assert(all(abs(vert.co.z) < 1e-6 for vert in bm.verts), "mesh relax should stay on the surface")

    uv = module.UVTarget(bpy.context, obj, bm)
    _assert(len(uv.groups) == len(bm.verts), f"grid UVs should form one island, got {len(uv.groups)} vertices")
    before = uv.points()
    pinned = next(i for i in range(len(uv.groups)) if not uv.topology.is_boundary(i))
    for loop in uv.groups[pinned]:
        loop[uv.uv_layer].pin_uv = True
    uv = module.UVTarget(bpy.context, obj, bm)
    _assert(not uv.topology.movable[pinned], "pinned UVs should not move")
    _tangle(uv, before, plane=0.08)
    _relax_fully(module, uv)
    layer = bm.loops.layers.uv.active
    for face in bm.faces:
        _assert(_signed_area([loop[layer].uv for loop in face.loops]) > 0.0, "relaxing should unfold every UV quad")
    after = uv.points()
    error = max((a - b).length for a, b in zip(after, before))
    _assert(error < 1e-3, f"relaxed grid UVs should return to even squares, off by {error}")

    bpy.ops.object.mode_set(mode="OBJECT")

    # The modal operator may receive a click or Escape after its Edit Mode
    # BMesh has been freed. Neither painting nor restoring is safe then.
    for mode in ("OBJECT", "EDIT"):
        if mode == "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        for target in (mesh, uv):
            _assert(not target.bm.is_valid, "the original Edit Mode BMesh should be freed")
            for event_type in ("LEFTMOUSE", "ESC"):
                finished = []

                def finish(result):
                    finished.append(result)
                    return result

                op = SimpleNamespace(target=target, _finish=finish)
                event = SimpleNamespace(type=event_type, value="PRESS")
                result = module.BLENDLET_OT_relax_brush.modal(op, bpy.context, event)
                _assert(result == {"FINISHED"}, "an invalid brush session should finish")
                _assert(finished == [{"FINISHED"}], "finishing should clean up the brush")
    bpy.ops.object.mode_set(mode="OBJECT")


def main() -> None:
    import bpy

    import blendlet
    from blendlet.host import get_host, reset_host

    reset_host()
    blendlet.register()
    host = get_host()
    directory = Path(tempfile.mkdtemp()).resolve()
    host.set_blendlets_directory(directory)

    path = directory / "relax_brush.py"
    shutil.copy(EXAMPLES / "relax_brush.py", path)
    record = host.reload_now(path)
    _assert(record is not None and record.error is None, f"relax_brush failed to load: {record and record.error_traceback}")
    _assert(hasattr(bpy.types, "BLENDLET_OT_relax_brush"), "the brush operator should be registered")
    settings = bpy.context.window_manager.blendlet_relax_brush
    settings.radius = 80.0
    _assert(record.context.state["radius"] == 80.0, "brush settings should live in ctx.state")

    record = host.reload_now(path)
    _assert(record.error is None, f"reload failed: {record.error_traceback}")
    _assert(
        bpy.context.window_manager.blendlet_relax_brush.radius == 80.0,
        "brush settings should survive a reload",
    )

    _check_relax_brush(record)

    path.unlink()
    host.reload_now(path)
    _assert(not hasattr(bpy.types, "BLENDLET_OT_relax_brush"), "deleting the file should unregister the brush")
    _assert(
        not hasattr(bpy.types.WindowManager, "blendlet_relax_brush"),
        "deleting the file should remove the settings property",
    )

    blendlet.unregister()
    print("BLENDLET_EXAMPLES_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
