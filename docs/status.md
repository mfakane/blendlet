# Status file

Blendlet writes machine-readable status for every watched folder, so a coding
agent or script can drop a `.py` file and tell whether its latest contents
loaded, without an MCP server or access to the Blender UI.

## Location

```text
<blendlets-directory>/.blendlet-status/status.json
<blendlets-directory>/.blendlet-status/errors/<name>.txt
```

For a trusted project folder the blendlets directory is
`<blend-folder>/.blendlet`, so its status is
`<blend-folder>/.blendlet/.blendlet-status/status.json`. Nothing is written to
an untrusted folder.

Files are replaced atomically. Each `.blendlet-status` folder contains a
`.gitignore` that ignores everything in it.

## Format

```json
{
  "schema_version": 1,
  "updated_at": "2026-10-03T06:00:00Z",
  "host": {
    "running": true,
    "blendlets_directory": "C:\\Users\\me\\blendlets"
  },
  "blendlets": [
    {
      "name": "hello",
      "path": "C:\\Users\\me\\blendlets\\hello.py",
      "state": "loaded",
      "kind": "persistent",
      "file_hash": "…",
      "active_hash": "…",
      "generation": 0,
      "updated_at": "2026-10-03T06:00:00Z",
      "active": true,
      "last_good": false,
      "error": null
    }
  ]
}
```

- `host.running` is false (and `blendlets` empty) after the add-on stops or the
  folder stops being watched.
- `state` is `reloading`, `loaded`, `ran`, `error`, or `disabled`.
- `kind` is `persistent`, `module`, `transient`, or null while unknown.
- `file_hash` is the SHA-256 of the last attempted source, or of a write that
  is still debouncing. `active_hash` is the SHA-256 of the running source.
- `error` is null or an object with `type`, `message`, `traceback`, and
  `file` (the sidecar path relative to the blendlets directory).

## Reading it

The file is rewritten only when something other than `updated_at` changes.
Poll until the entry settles rather than sleeping once; the debounce is 0.4
seconds and the host polls every 0.2 seconds.

- **Persistent success:** `state == "loaded"`, `active == true`,
  `active_hash == file_hash`, and `file_hash` equals your SHA-256 of the file.
- **One-shot success:** `state == "ran"` and `file_hash` equals your SHA-256.
- **Still working:** `state == "reloading"`.
- **Failed, old version running:** `state == "error"`, `last_good == true`,
  `active == true`. `file_hash` is the failed candidate and `active_hash` the
  version still running.
- **Failed, nothing running:** `state == "error"` and `active == false`.
