# Blendlet

Grow small Blender tools without restarting Blender.

A blendlet is a single Python file. Drop it into a folder and Blendlet loads
it; save an edit and it reloads in place. Buttons, shortcuts, and viewport
overlays it registered are cleaned up and registered again, so nothing gets
duplicated. If an edit breaks, the last working version keeps running.

It also works well with coding agents: an agent can write a blendlet and read
a status file to see whether it loaded, without driving Blender itself.

## Install

Requires Blender 4.2 or later.

1. Build the extension zip (see [Development](docs/development.md)).
2. In Blender, open *Edit → Preferences → Extensions → Install from Disk* and
   choose `blendlet-0.1.0.zip`.
3. Enable **Blendlet**. Blendlets are read from `~/blendlets` unless you set
   **Blendlets Directory** in the add-on preferences.

## Your first blendlet

Save this as `~/blendlets/hello.py`:

```python
import bpy


class BLENDLET_OT_hello(bpy.types.Operator):
    bl_idname = "blendlet.hello"
    bl_label = "Say Hello"

    def execute(self, context):
        self.report({"INFO"}, "Hello from a blendlet")
        return {"FINISHED"}


class BLENDLET_PT_hello(bpy.types.Panel):
    bl_label = "Hello"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hello"

    def draw(self, context):
        self.layout.operator(BLENDLET_OT_hello.bl_idname)


def register(ctx):
    ctx.register_class(BLENDLET_OT_hello)
    ctx.register_class(BLENDLET_PT_hello)
```

A **Hello** tab appears in the 3D View sidebar (press `N`). Change the label or
message and save; the panel updates within a second. Delete the file and the
tab disappears.

A file with `run(ctx)` instead of `register(ctx)` is a one-shot script: it runs
each time you save it and is not kept loaded.

See [Writing blendlets](docs/writing-blendlets.md) for timers, shortcuts,
draw handlers, values that survive reloads, and what happens when a reload
fails.

## Examples

Copy a file from [`examples/`](examples/) into your blendlets folder to try it.

- [`relax_brush.py`](examples/relax_brush.py) — a brush for Edit Mode meshes
  and the UV Editor that untangles vertices and evens quads out toward
  rectangles. Press Shift+Alt+R or use the **Relax** sidebar tab, drag to
  relax, Ctrl/Shift+Wheel for radius/strength, right click to finish.

## The Blendlet panel

The **Blendlet** tab in the 3D View sidebar lists every blendlet with whether
its latest edit loaded or failed. From there you can:

- **Reload** a blendlet, or **Reload All**
- **Disable** a blendlet without deleting the file, and **Enable** it again
- read the error of a failed reload and **Copy Error** to the clipboard
- open the blendlets folder

## Blendlets that travel with a .blend file

A `.blend` file can bring its own tools: put blendlets in a `.blendlet` folder
next to the saved file.

```text
my-project/
├── scene.blend
└── .blendlet/
    └── rig_helpers.py
```

A blendlet can run any Python code, and a `.blend` file can come from anyone,
so Blendlet never loads a folder it does not know. The panel lists its files
and asks first:

- **Trust for Now** loads them until Blender quits or the add-on is disabled.
- **Trust and Remember** also keeps trusting that folder in later sessions.
  Remembered folders are listed in the add-on preferences, where you can remove
  them again.

Trust belongs to the folder's location, not its contents, so only remember
folders whose contents you control. Opening another `.blend` file unloads the
previous file's blendlets.

## Using a coding agent

Point the agent at
[`skills/blendlet-development/SKILL.md`](skills/blendlet-development/SKILL.md).
It explains how to write blendlets and how to confirm a load through
[the status file](docs/status.md). Trusting a project folder stays a human
decision.

## Documentation

- [Writing blendlets](docs/writing-blendlets.md)
- [Status file for agents and scripts](docs/status.md)
- [Development](docs/development.md)
