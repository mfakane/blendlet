# Writing blendlets

## Where blendlets are found

A blendlet is a top-level, non-hidden `.py` file in a watched folder:

- the user blendlets directory (add-on preference **Blendlets Directory**,
  `~/blendlets` when empty), and
- the `.blendlet` folder next to the open `.blend` file, after it is trusted.

Subfolders and files starting with `.` are ignored. Writes are debounced for
0.4 seconds, so an editor saving in several steps causes one reload. If the
`.blendlet` folder is the user blendlets directory itself, it is watched once,
as the user directory.

## Execution contract

- **`register(ctx)`** makes a persistent tool. It stays loaded until the file
  is edited, deleted, or disabled, or the add-on stops.
- **`unregister(ctx)`** (optional) runs before the tool is unloaded.
- **`run(ctx)`** without `register` makes a one-shot script. It runs when the
  file is created or saved and is then dropped.

If both exist, `register` wins. Ordinary `bpy` is available; there is no
custom API besides `ctx`.

## Resources owned by `ctx`

Register Blender resources through `ctx`, and Blendlet undoes them on reload,
deletion, disable, and add-on shutdown. A tool that only uses these does not
need `unregister()`.

| Resource | Add | Remove early |
| --- | --- | --- |
| Classes (operators, panels, …) | `ctx.register_class(cls)` | `ctx.unregister_class(cls)` |
| Timers | `ctx.add_timer(fn, first_interval=0.0, persistent=False)` | `ctx.remove_timer(fn)` |
| Draw handlers | `ctx.add_draw_handler(space, fn, args, region_type="WINDOW", draw_type="POST_PIXEL")` | `ctx.remove_draw_handler(handle)` |
| Keymaps | `ctx.add_keymap(name, space_type=..., region_type=...)` | `ctx.remove_keymap(km)` |
| Keymap items | `ctx.add_keymap_item(km, idname, type=..., value="PRESS", ...)` | `ctx.remove_keymap_item(item)` |
| App handlers | `ctx.add_handler(bpy.app.handlers.<list>, fn)` | `ctx.remove_handler(list, fn)` |
| RNA subscriptions | `ctx.subscribe_rna(key, fn, args)` | — |
| Anything else | `ctx.add_cleanup(fn)` | — |

Cleanup runs in reverse order, each step isolated from failures in the others.
`ctx.dispose()` runs them all; it is idempotent and also runs after
`unregister(ctx)`. Resources registered directly through `bpy` are not tracked,
so clean those up yourself.

```python
import bpy


def register(ctx):
    ctx.register_class(MyOperator)
    ctx.add_timer(tick, persistent=True)
    km = ctx.add_keymap("3D View", space_type="VIEW_3D")
    ctx.add_keymap_item(km, "mesh.knife_tool", type="K")
    ctx.add_draw_handler(bpy.types.SpaceView3D, draw, ())
    ctx.add_handler(bpy.app.handlers.depsgraph_update_post, on_update)
```

## Values that survive reloads: `ctx.state`

`ctx.state` is a dict owned by Blendlet, one per blendlet file path. Values
survive a reload of that file and disabling/enabling it. Deleting the file,
leaving the `.blend` file of a project blendlet, disabling the add-on, or
quitting Blender drops them. Storage is in memory only.

Keep small plain values there, such as a brush radius. Do not store objects
defined by the module (classes, callbacks, `ctx` itself); recreate those in
every `register`.

```python
def register(ctx):
    radius = ctx.state.setdefault("radius", 8.0)
```

## When a reload fails

- A syntax or import error leaves the previous working version running.
- If the new version imports but `register()` fails, the previous version is
  registered again when that succeeds.
- Either way the panel and the [status file](status.md) show the failure and
  its traceback. Saving the same broken contents again is not retried; change
  the file or press **Reload**.

## Disable

Disabling a blendlet in the panel unloads it and ignores later edits until it
is enabled or reloaded. It does not delete the file and lasts until the add-on
stops.
