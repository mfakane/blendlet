# Development

## Layout

- `src/blendlet/` — the add-on (extension package)
- `tests/` — CPython unit tests plus harnesses that run inside Blender
- `skills/blendlet-development/` — agent skill for writing blendlets

## Build

```text
blender --command extension build --source-dir src/blendlet --output-dir dist
```

This produces `dist/blendlet-<version>.zip` for *Install from Disk*.

## Tests

```text
uv sync
uv run python -m unittest discover -s tests -v
```

When a Blender executable is found (`BLENDER_EXECUTABLE`, `BLENDER`, `PATH`,
or the default Windows install path), the suite also validates and builds the
extension and runs the host, panel, and installed-add-on harnesses in a
background Blender process. Otherwise those tests are skipped.
