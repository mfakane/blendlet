---
name: blendlet-development
description: Create, edit, and troubleshoot Blendlet Python tools for this repository. Use whenever a task involves a blendlet, Blender hot reload, register(ctx), run(ctx), ctx.state, status.json, or ctx-owned Blender resources.
compatibility: Blender 4.2+ and this repository's Blendlet host
---

# Blendlet development

Use the repository implementation as the contract. Treat each top-level,
non-hidden `.py` file in the configured directory as one blendlet. The default
directory is `~/blendlets`; use ordinary `bpy`, not a custom DSL.

## Choose the execution contract

- Define `register(ctx)` for a persistent tool. It remains loaded until the
  file is reloaded, deleted, disabled, or the host stops.
- If both functions exist, `register(ctx)` wins; do not call `run(ctx)`.
- Define `run(ctx)` for a transient one-shot. It runs on create/modify and is
  not kept loaded.
- Add `unregister(ctx)` only when the tool has explicit cleanup beyond
  resources owned through `ctx`. The host calls it, then calls `ctx.dispose()`.

## Own resources through `ctx`

Register host-supported resources through the context so reload, deletion, and
host disable clean them up. Available ownership APIs are:

`register_class`/`unregister_class`, `add_timer`/`remove_timer`,
`add_draw_handler`/`remove_draw_handler`, `add_keymap`, `add_keymap_item`,
`remove_keymap`, `remove_keymap_item`, `add_handler`/`remove_handler`,
`subscribe_rna`, `add_cleanup`, and `dispose`.

Cleanup is idempotent, LIFO, and isolates exceptions. Direct `bpy` registration
is not owned by the host; avoid it or clean it yourself. Do not write a
needless `unregister` just to undo resources added with `ctx`.

## Preserve state correctly

Use `ctx.state` for small primitive/config values only. It is in-memory and
scoped by the blendlet path: it survives reload and individual disable/enable,
but is dropped on deletion and host stop/restart. Failed candidate mutations
are not rolled back. Recreate runtime objects on every `register`; never store
module-defined Blender objects or callbacks in state.

### Transient example

```python
import bpy


def run(ctx):
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.context.object.name = f"{ctx.name}_cube"
```

### Persistent operator and keymap example

```python
import bpy

_ctx = None


class BLENDLET_OT_increment(bpy.types.Operator):
    bl_idname = "blendlet.increment"
    bl_label = "Increment Blendlet Count"

    def execute(self, context):
        _ctx.state["count"] = _ctx.state.get("count", 0) + 1
        self.report({"INFO"}, f"Count: {_ctx.state['count']}")
        return {"FINISHED"}


def register(ctx):
    global _ctx
    _ctx = ctx
    ctx.register_class(BLENDLET_OT_increment)
    ctx.state.setdefault("count", 0)
    km = ctx.add_keymap("Window", space_type="EMPTY")
    ctx.add_keymap_item(km, BLENDLET_OT_increment.bl_idname, type="I")
```

The module-level `_ctx` is a runtime reference recreated on every `register`;
the count survives because only its primitive value is in `ctx.state`. The host
owns the class and keymap, so no `unregister` is needed. Never put `_ctx` itself
in `ctx.state`.

## Observe hot reload

Poll `<blendlets-directory>/.blendlet/status.json`; do not infer usability from
one sleep or from a successful file write. The watcher debounce is currently
`0.4` seconds and the host timer polls every `0.2` seconds, so repeatedly poll
until the relevant state and hash settle.

The JSON has `schema_version: 1`, `host.running`,
`host.blendlets_directory`, and `blendlets` entries. Each entry includes
`name`, `path`, `state`, `kind`, `file_hash`, `active_hash`, `generation`,
`updated_at`, `active`, `last_good`, and `error`. Error tracebacks also live at
`.blendlet/errors/<stem>.txt`.

Treat success as follows:

- Persistent: `state == "loaded"`, `active == true`,
  `active_hash == file_hash`, and `file_hash` equals a locally computed
  SHA-256 of the exact source file.
- Transient: `state == "ran"` and `file_hash` equals that local SHA-256.
- `reloading` is still in progress. `error` with `last_good == true` means
  the old version is still running, not that the candidate is usable.

Failed unchanged source is not retried automatically. Disabled files ignore
edits; deleted entries disappear. The host status must also say it is running
and report the expected configured directory.

## Promote safely

Start with a transient script for a one-shot experiment. Promote it to a
persistent `register(ctx)` tool only after moving registrations to `ctx`,
keeping `ctx.state` primitive, and recreating runtime objects on reload. After
each edit, check status, local hash, active/last-good fields, and the actual
Blender UI behavior. Ask a human to verify usability: the expected panel or
operator appears, the shortcut works in the intended editor, reload does not
duplicate UI or callbacks, disable removes the tool, and the previous version
remains usable after a failed candidate. Status alone is not a UX test.

## Troubleshoot

- **Import or register failure:** read `error` and
  `.blendlet/errors/<stem>.txt`; fix the traceback, then change the source (or
  manually reload). An unchanged failed hash is intentionally not retried.
- **Stale or missing status:** confirm `host.running`, the configured absolute
  directory, the `.blendlet` directory permissions, and that polling has
  crossed both debounce and host-timer intervals.
- **Wrong path:** inspect `host.blendlets_directory`; only top-level,
  non-hidden `.py` files there are discovered. The empty preference means
  `~/blendlets`.
- **Disabled file:** re-enable it or manually reload it; edits while disabled
  are ignored.
- **Last-good entry:** `state == "error"`, `last_good == true`, and `active ==
  true` means the old source is running. Do not declare the candidate loaded.
- **Hash does not match:** compute SHA-256 from the exact file contents and
  wait for a stable status entry; do not trust timestamps or a single poll.
- **Direct-`bpy` leak:** remove the direct registration or add explicit cleanup;
  the host cannot dispose resources it did not receive through `ctx`.
