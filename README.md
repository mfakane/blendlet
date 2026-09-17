# Blendlet

Hot-reloadable micro-tool host for Blender. A blendlet is a small Python file
that can grow into a reusable tool without restarting Blender.

This repository currently bootstraps the host add-on: package metadata,
blendlets-directory preferences, and a start/stop lifecycle. File watching and
module loading are follow-up work.

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

## Tests

```text
uv sync
uv run python -m unittest discover -s tests -v
```

If Blender is on `PATH` (or `BLENDER` points at `blender.exe`), the suite also
validates the extension package and runs an enable/disable cycle in a
background Blender process.
