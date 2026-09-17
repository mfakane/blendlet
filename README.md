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

A blendlet is a top-level ``.py`` file in that directory. Persistent tools
define ``register(ctx)`` and may define ``unregister(ctx)``. One-shot scripts
define ``run(ctx)`` instead; they execute on create/modify and are not kept as
loaded add-ons. Ordinary ``bpy`` remains available.

## Tests

```text
uv sync
uv run python -m unittest discover -s tests -v
```

If Blender is on `PATH` (or `BLENDER` points at `blender.exe`), the suite also
validates the extension package and runs an enable/disable cycle in a
background Blender process.
