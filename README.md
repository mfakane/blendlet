# Blendlet

Hot-reloadable micro-tool host for Blender. A blendlet is a small Python file
that can grow into a reusable tool without restarting Blender.

This repository currently bootstraps the host add-on: package metadata,
blendlets-directory preferences, a start/stop lifecycle, discovery of
top-level ``.py`` blendlets with debounced create/modify/delete events, and
the module execution contract (``register(ctx)``, optional ``unregister(ctx)``,
and transient ``run(ctx)``).

## Install

Requires Blender 4.2 or later.

```text
blender --command extension build --source-dir src/blendlet --output-dir dist
```

In Blender: *Edit → Preferences → Extensions → Install from Disk* and choose
the built `blendlet-0.1.0.zip`. Enable the add-on, then set **Blendlets
Directory** in its preferences. An empty value resolves to `~/blendlets`.

Disable the add-on to stop the host. Enable it again without restarting Blender
to start it with the current preferences.

The 3D View sidebar (N-panel, **Blendlet** tab) lists discovered blendlets,
whether the latest edit loaded, failed, or is disabled, and the latest reload
error. Reload or enable/disable a tool from there. The configured blendlets
directory is shown and can be opened in the file browser.

A blendlet is a top-level ``.py`` file in that directory. Persistent tools
define ``register(ctx)`` and may define ``unregister(ctx)``. One-shot scripts
define ``run(ctx)`` instead; they execute on create/modify and are not kept as
loaded add-ons. Ordinary ``bpy`` remains available.

Register Blender resources through ``ctx`` so reload, delete, and add-on
disable undo them automatically. A simple blendlet does not need
``unregister()`` merely to tear down those resources. ``ctx.dispose()`` is
idempotent and still runs after an optional ``unregister(ctx)``.

``ctx.state`` is a host-owned dict for small values such as a brush radius.
It is keyed by blendlet path, so two files cannot share a slot. Values
survive a hot reload of that file. Deleting the file, disabling the add-on,
or restarting Blender drops the slot. v0.1 storage is in-memory; do not
keep module-defined objects there.

A syntax or import error while a persistent tool is already loaded keeps that
last working version active. If the candidate imports but ``register()`` fails
after the old version is disposed, the last working version is restored when
that restoration succeeds. Failures keep an exception and traceback on the
loaded record.

The host writes machine-readable status to
``<blendlets-directory>/.blendlet/status.json`` so a coding agent can drop a
``.py`` file, wait for the debounce window, and tell whether the latest
contents loaded. ``file_hash`` is the SHA-256 of the last attempted source
(or of a still-debouncing write). ``state`` is ``reloading``, ``loaded``,
``ran``, or ``error``. When a reload fails and last-good stays active,
``last_good`` is true, ``active`` is true, ``file_hash`` is the failed
candidate, and ``active_hash`` is the running last-good source. Tracebacks
are in the JSON ``error`` object and in
``.blendlet/errors/<name>.txt``.

```python
import bpy


def register(ctx):
    ctx.register_class(MyOperator)
    ctx.add_timer(tick, persistent=True)
    km = ctx.add_keymap("3D View", space_type="VIEW_3D")
    ctx.add_keymap_item(km, "mesh.knife_tool", type="K")
    ctx.add_draw_handler(bpy.types.SpaceView3D, draw, ())
    ctx.add_handler(bpy.app.handlers.depsgraph_update_post, on_update)
    radius = ctx.state.get("radius", 8.0)
    ctx.state["radius"] = radius
```

## Tests

```text
uv sync
uv run python -m unittest discover -s tests -v
```

If Blender is on `PATH` (or `BLENDER` points at `blender.exe`), the suite also
validates the extension package and runs host lifecycle and status-panel checks
in a background Blender process (`blender --background --factory-startup`).
