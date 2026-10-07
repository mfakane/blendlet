"""Relax brush for Edit Mode meshes and UVs.

Paint over tangled vertices to even them out. Quads are pulled toward
rectangles: each step blends the average of a vertex's neighbors with the
corner position that would make every adjacent quad a parallelogram.

- 3D View, Edit Mode: vertices slide along the surface.
- UV Editor: UV vertices move inside their island. Pinned UVs stay put.

Start it with Shift+Alt+R or the **Relax** sidebar tab, then:

- drag with the left mouse button to relax,
- Ctrl+Wheel to change the radius, Shift+Wheel to change the strength,
- Right click or Enter to finish (one undo step), Esc to cancel.

Radius, strength, and boundary pinning live in ``ctx.state``, so they survive
edits to this file.
"""

from __future__ import annotations

import math

import bmesh
import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

DEFAULTS = {"radius": 60.0, "strength": 0.5, "pin_boundary": True}
CIRCLE_SEGMENTS = 48

_ctx = None


def _setting(name):
    return _ctx.state.get(name, DEFAULTS[name])


def _set_setting(name, value):
    _ctx.state[name] = value


# --- Relaxation (shared by meshes and UVs) ---------------------------------


class Topology:
    """Index-based connectivity for one relax session.

    ``quads[i]`` holds ``(a, c, b)`` for every quad around vertex ``i``: ``a``
    and ``b`` are its neighbors in that quad and ``c`` the opposite corner.
    """

    def __init__(self, count):
        self.neighbors = [set() for _ in range(count)]
        self.boundary_neighbors = [set() for _ in range(count)]
        self.quads = [[] for _ in range(count)]
        self.movable = [True] * count

    def add_edge(self, i, j, *, boundary):
        self.neighbors[i].add(j)
        self.neighbors[j].add(i)
        if boundary:
            self.boundary_neighbors[i].add(j)
            self.boundary_neighbors[j].add(i)

    def add_face(self, corners):
        if len(corners) != 4:
            return
        for k, i in enumerate(corners):
            self.quads[i].append((corners[(k + 1) % 4], corners[(k + 2) % 4], corners[k - 1]))

    def is_boundary(self, i):
        return bool(self.boundary_neighbors[i])


def falloff(t):
    """Smooth brush falloff: 1 at the center, 0 at the rim."""
    return (1.0 - t * t) ** 2 if t < 1.0 else 0.0


def _mean(points):
    total = points[0].copy()
    for point in points[1:]:
        total += point
    return total / len(points)


def relax_target(points, topology, i, *, pin_boundary):
    """Where vertex ``i`` would like to be, or None to leave it alone."""
    if topology.is_boundary(i):
        if pin_boundary or len(topology.boundary_neighbors[i]) < 2:
            return None
        # Slide along the boundary instead of shrinking the island inward.
        return _mean([points[j] for j in topology.boundary_neighbors[i]])
    if not topology.neighbors[i]:
        return None
    target = _mean([points[j] for j in topology.neighbors[i]])
    if topology.quads[i]:
        corners = [points[a] + points[b] - points[c] for a, c, b in topology.quads[i]]
        target = (target + _mean(corners)) * 0.5
    return target


def relax_step(points, topology, weights, *, strength, pin_boundary, constrain=None):
    """Move weighted vertices toward their targets. Returns ``{index: new_point}``.

    Targets are computed from the old positions so the result does not depend
    on vertex order.
    """
    moved = {}
    for i, weight in weights.items():
        target = relax_target(points, topology, i, pin_boundary=pin_boundary)
        if target is None:
            continue
        delta = (target - points[i]) * (strength * weight)
        if constrain is not None:
            delta = constrain(i, delta)
        moved[i] = points[i] + delta
    return moved


# --- Mesh and UV adapters --------------------------------------------------


class MeshTarget:
    def __init__(self, obj, bm):
        self.obj = obj
        self.bm = bm
        bm.verts.index_update()
        bm.verts.ensure_lookup_table()
        self.verts = list(bm.verts)
        self.topology = Topology(len(self.verts))
        for edge in bm.edges:
            a, b = edge.verts
            self.topology.add_edge(a.index, b.index, boundary=edge.is_boundary)
        for face in bm.faces:
            self.topology.add_face([vert.index for vert in face.verts])
        for vert in self.verts:
            self.topology.movable[vert.index] = not vert.hide
        self.original = [vert.co.copy() for vert in self.verts]

    def points(self):
        return [vert.co.copy() for vert in self.verts]

    def screen_weights(self, context, region, center, radius):
        rv3d = region.data
        matrix = self.obj.matrix_world
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        xray = context.space_data.shading.show_xray
        eye = rv3d.view_matrix.inverted().translation
        view_dir = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        weights = {}
        for vert in self.verts:
            if not self.topology.movable[vert.index]:
                continue
            world = matrix @ vert.co
            screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
            if screen is None:
                continue
            distance = (screen - center).length
            if distance >= radius:
                continue
            if not xray:
                facing = (world - eye) if rv3d.is_perspective else view_dir
                if (normal_matrix @ vert.normal).dot(facing) > 0.0:
                    continue
            weights[vert.index] = falloff(distance / radius)
        return weights

    def constrain(self, i, delta):
        normal = self.verts[i].normal
        return delta - normal * delta.dot(normal)

    def apply(self, moved):
        for i, point in moved.items():
            self.verts[i].co = point
        self.bm.normal_update()
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)

    def restore(self):
        self.apply(dict(enumerate(self.original)))


class UVTarget:
    """UV vertices: loops sharing a mesh vertex and the same UV coordinate."""

    def __init__(self, context, obj, bm):
        self.obj = obj
        self.bm = bm
        self.uv_layer = bm.loops.layers.uv.active
        sync = context.scene.tool_settings.use_uv_select_sync
        faces = [face for face in bm.faces if not face.hide and (sync or face.select)]

        ids = {}
        self.groups = []
        loop_ids = {}
        for face in faces:
            for loop in face.loops:
                uv = loop[self.uv_layer].uv
                key = (loop.vert.index, round(uv.x, 6), round(uv.y, 6))
                group = ids.get(key)
                if group is None:
                    group = ids[key] = len(self.groups)
                    self.groups.append([])
                self.groups[group].append(loop)
                loop_ids[loop] = group

        self.topology = Topology(len(self.groups))
        edge_faces = {}
        for face in faces:
            corners = [loop_ids[loop] for loop in face.loops]
            self.topology.add_face(corners)
            for k, i in enumerate(corners):
                edge = frozenset((i, corners[(k + 1) % len(corners)]))
                edge_faces[edge] = edge_faces.get(edge, 0) + 1
        for edge, count in edge_faces.items():
            if len(edge) == 2:
                i, j = edge
                self.topology.add_edge(i, j, boundary=count == 1)
        for i, group in enumerate(self.groups):
            self.topology.movable[i] = not any(loop[self.uv_layer].pin_uv for loop in group)
        self.original = self.points()

    def points(self):
        return [group[0][self.uv_layer].uv.copy() for group in self.groups]

    def screen_weights(self, context, region, center, radius):
        view2d = region.view2d
        weights = {}
        for i, point in enumerate(self.points()):
            if not self.topology.movable[i]:
                continue
            screen = Vector(view2d.view_to_region(point.x, point.y, clip=False))
            distance = (screen - center).length
            if distance < radius:
                weights[i] = falloff(distance / radius)
        return weights

    constrain = None

    def apply(self, moved):
        for i, point in moved.items():
            for loop in self.groups[i]:
                loop[self.uv_layer].uv = point
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)

    def restore(self):
        self.apply(dict(enumerate(self.original)))


# --- Brush operator --------------------------------------------------------


def _window_region(area, event):
    """The main region under the mouse, or the first one (e.g. from a sidebar button)."""
    regions = [region for region in area.regions if region.type == "WINDOW"]
    for region in regions:
        inside_x = region.x <= event.mouse_x < region.x + region.width
        inside_y = region.y <= event.mouse_y < region.y + region.height
        if inside_x and inside_y:
            return region
    return regions[0]


def _is_uv_editor(context):
    space = context.space_data
    return context.area.type == "IMAGE_EDITOR" and space.mode == "UV"


def _draw_circle(op):
    if bpy.context.area is None or bpy.context.area.as_pointer() != op.area_pointer:
        return
    center = op.mouse
    radius = _setting("radius")
    points = [
        (center.x + radius * cos, center.y + radius * sin)
        for cos, sin in op.unit_circle
    ]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": points})
    gpu.state.blend_set("ALPHA")
    shader.uniform_float("color", (1.0, 1.0, 1.0, 0.4 + 0.5 * _setting("strength")))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


class BLENDLET_OT_relax_brush(bpy.types.Operator):
    bl_idname = "blendlet.relax_brush"
    bl_label = "Relax Brush"
    bl_description = "Paint to relax tangled vertices or UVs toward an even quad grid"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        if obj is None or obj.type != "MESH" or context.area is None:
            return False
        return context.area.type == "VIEW_3D" or _is_uv_editor(context)

    def invoke(self, context, event):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        if _is_uv_editor(context):
            if bm.loops.layers.uv.active is None:
                self.report({"ERROR"}, "The mesh has no UV map")
                return {"CANCELLED"}
            self.target = UVTarget(context, obj, bm)
        else:
            self.target = MeshTarget(obj, bm)

        self.area = context.area
        self.area_pointer = context.area.as_pointer()
        self.region = _window_region(context.area, event)
        self.stroking = False
        self.mouse = self._region_mouse(event)
        self.unit_circle = [
            (math.cos(2 * math.pi * k / CIRCLE_SEGMENTS), math.sin(2 * math.pi * k / CIRCLE_SEGMENTS))
            for k in range(CIRCLE_SEGMENTS + 1)
        ]
        space = bpy.types.SpaceImageEditor if self.area.type == "IMAGE_EDITOR" else bpy.types.SpaceView3D
        self.draw_space = space
        self.draw_handle = _ctx.add_draw_handler(space, _draw_circle, (self,), draw_type="POST_PIXEL")
        self._update_header()
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        # Leaving Edit Mode frees the BMesh, including every cached UV loop.
        # Finish before handling even Escape: restoring would touch freed data.
        if not self.target.bm.is_valid or context.edit_object != self.target.obj:
            return self._finish({"FINISHED"})

        self.mouse = self._region_mouse(event)
        self.area.tag_redraw()

        if event.type in {"ESC"} and event.value == "PRESS":
            self.target.restore()
            return self._finish({"CANCELLED"})
        if event.type in {"RIGHTMOUSE", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return self._finish({"FINISHED"})

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and (event.ctrl or event.shift):
            up = event.type == "WHEELUPMOUSE"
            if event.ctrl:
                radius = _setting("radius") * (1.1 if up else 1 / 1.1)
                _set_setting("radius", min(max(radius, 5.0), 1000.0))
            else:
                strength = _setting("strength") + (0.05 if up else -0.05)
                _set_setting("strength", min(max(strength, 0.05), 1.0))
            self._update_header()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            self.stroking = event.value == "PRESS" and self._inside_region()
            if self.stroking:
                self._dab(context)
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            if self.stroking:
                self._dab(context)
            return {"RUNNING_MODAL"}

        # Wheel, middle mouse, trackpad, and NDOF keep navigating the view.
        return {"PASS_THROUGH"}

    def _region_mouse(self, event):
        return Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))

    def _inside_region(self):
        return 0 <= self.mouse.x < self.region.width and 0 <= self.mouse.y < self.region.height

    def _dab(self, context):
        target = self.target
        weights = target.screen_weights(context, self.region, self.mouse, _setting("radius"))
        if not weights:
            return
        moved = relax_step(
            target.points(),
            target.topology,
            weights,
            strength=_setting("strength"),
            pin_boundary=_setting("pin_boundary"),
            constrain=target.constrain,
        )
        target.apply(moved)

    def _update_header(self):
        self.area.header_text_set(
            f"Relax Brush  radius {_setting('radius'):.0f}px  strength {_setting('strength'):.2f}"
            "  |  LMB: relax  Ctrl+Wheel: radius  Shift+Wheel: strength  RMB/Enter: done  Esc: cancel"
        )

    def _finish(self, result):
        _ctx.remove_draw_handler(self.draw_handle)
        self.area.header_text_set(None)
        self.area.tag_redraw()
        return result


# --- Settings and panels ---------------------------------------------------


def _float_setting(key, **kwargs):
    return bpy.props.FloatProperty(
        get=lambda _self: _setting(key),
        set=lambda _self, value: _set_setting(key, value),
        **kwargs,
    )


class BLENDLET_PG_relax_brush(bpy.types.PropertyGroup):
    radius: _float_setting("radius", name="Radius", subtype="PIXEL", min=5.0, max=1000.0)
    strength: _float_setting("strength", name="Strength", subtype="FACTOR", min=0.05, max=1.0)
    pin_boundary: bpy.props.BoolProperty(
        name="Pin Boundary",
        description="Keep mesh and UV island boundaries in place",
        get=lambda _self: _setting("pin_boundary"),
        set=lambda _self, value: _set_setting("pin_boundary", value),
    )


def _draw_settings(layout, context):
    settings = context.window_manager.blendlet_relax_brush
    layout.operator(BLENDLET_OT_relax_brush.bl_idname, icon="BRUSH_DATA")
    layout.prop(settings, "radius")
    layout.prop(settings, "strength")
    layout.prop(settings, "pin_boundary")


class BLENDLET_PT_relax_brush_mesh(bpy.types.Panel):
    bl_label = "Relax Brush"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Relax"

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH"

    def draw(self, context):
        _draw_settings(self.layout, context)


class BLENDLET_PT_relax_brush_uv(bpy.types.Panel):
    bl_label = "Relax Brush"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Relax"

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and _is_uv_editor(context)

    def draw(self, context):
        _draw_settings(self.layout, context)


def register(ctx):
    global _ctx
    _ctx = ctx
    ctx.register_class(BLENDLET_OT_relax_brush)
    ctx.register_class(BLENDLET_PG_relax_brush)
    bpy.types.WindowManager.blendlet_relax_brush = bpy.props.PointerProperty(
        type=BLENDLET_PG_relax_brush
    )
    ctx.add_cleanup(lambda: delattr(bpy.types.WindowManager, "blendlet_relax_brush"))
    ctx.register_class(BLENDLET_PT_relax_brush_mesh)
    ctx.register_class(BLENDLET_PT_relax_brush_uv)
    for keymap_name in ("Mesh", "UV Editor"):
        keymap = ctx.add_keymap(keymap_name)
        ctx.add_keymap_item(keymap, BLENDLET_OT_relax_brush.bl_idname, type="R", shift=True, alt=True)
