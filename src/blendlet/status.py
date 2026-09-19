"""Machine-readable host status for coding agents.

The hot-reload loop is observable from files under the blendlets directory,
without an MCP server. The host writes ``.blendlet/status.json`` atomically and
mirrors the latest traceback to ``.blendlet/errors/<name>.txt``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discovery import PendingFile, fingerprint_file
from .lifecycle import Blendlet

STATE_LABELS = {
    "loaded": "Loaded",
    "ran": "Ran",
    "error": "Failed",
    "reloading": "Reloading",
    "disabled": "Disabled",
}

ERROR_PREVIEW_LINES = 16

SCHEMA_VERSION = 1
STATUS_DIRNAME = ".blendlet"
STATUS_FILENAME = "status.json"
ERRORS_DIRNAME = "errors"


def status_directory(blendlets_directory: Path) -> Path:
    return Path(blendlets_directory) / STATUS_DIRNAME


def status_file(blendlets_directory: Path) -> Path:
    return status_directory(blendlets_directory) / STATUS_FILENAME


def error_file(blendlets_directory: Path, blendlet_path: Path) -> Path:
    return status_directory(blendlets_directory) / ERRORS_DIRNAME / f"{Path(blendlet_path).stem}.txt"


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` via a same-directory temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def display_state(entry: dict[str, Any]) -> str:
    """Human label for a status entry, including last-good failures."""
    state = entry.get("state")
    if state == "error" and entry.get("last_good"):
        return "Failed — last good active"
    if isinstance(state, str):
        return STATE_LABELS.get(state, state)
    return "Unknown"


def latest_edit_loaded(entry: dict[str, Any]) -> bool:
    """True when the latest on-disk contents loaded or ran successfully."""
    return entry.get("state") in {"loaded", "ran"}


def error_preview_lines(
    entry: dict[str, Any],
    *,
    max_lines: int = ERROR_PREVIEW_LINES,
) -> list[str]:
    """Traceback lines a panel can show without a terminal."""
    error = entry.get("error")
    if not error:
        return []
    traceback_text = (error.get("traceback") or "").rstrip()
    if traceback_text:
        lines = traceback_text.splitlines()
        if len(lines) > max_lines:
            return ["…"] + lines[-max_lines:]
        return lines
    type_name = error.get("type") or "Error"
    message = error.get("message") or ""
    if message:
        return [f"{type_name}: {message}"]
    return [type_name]


def build_status(
    *,
    blendlets_directory: Path,
    running: bool,
    records: tuple[Blendlet, ...] = (),
    pending: tuple[PendingFile, ...] = (),
    disabled: tuple[Path, ...] = (),
    now: datetime,
) -> dict[str, Any]:
    directory = Path(blendlets_directory)
    entries = [] if not running else _blendlet_entries(records, pending, disabled)
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": format_timestamp(now),
        "host": {
            "running": running,
            "blendlets_directory": str(directory) if running else None,
        },
        "blendlets": entries,
    }


def write_status(
    blendlets_directory: Path,
    *,
    running: bool,
    records: tuple[Blendlet, ...] = (),
    pending: tuple[PendingFile, ...] = (),
    disabled: tuple[Path, ...] = (),
    now: datetime | None = None,
    previous_signature: str | None = None,
) -> tuple[Path, str, bool]:
    """Write status.json and error sidecars.

    Returns ``(path, signature, written)``. ``written`` is False when the
    payload matches ``previous_signature`` aside from ``updated_at``.
    """
    moment = datetime.now(timezone.utc) if now is None else now
    payload = build_status(
        blendlets_directory=blendlets_directory,
        running=running,
        records=records,
        pending=pending,
        disabled=disabled,
        now=moment,
    )
    signature = _signature(payload)
    path = status_file(blendlets_directory)
    if signature == previous_signature:
        return path, signature, False
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, text)
    _sync_error_files(blendlets_directory, payload["blendlets"])
    return path, signature, True


def _signature(payload: dict[str, Any]) -> str:
    comparable = dict(payload)
    comparable.pop("updated_at", None)
    return json.dumps(comparable, sort_keys=True, ensure_ascii=False)


def _blendlet_entries(
    records: tuple[Blendlet, ...],
    pending: tuple[PendingFile, ...],
    disabled: tuple[Path, ...] = (),
) -> list[dict[str, Any]]:
    disabled_set = {Path(path) for path in disabled}
    pending_by_path = {item.path: item.fingerprint for item in pending}
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for record in records:
        if record.path in disabled_set:
            continue
        reloading = record.path in pending_by_path
        entries.append(
            _entry_from_record(
                record,
                reloading=reloading,
                pending_hash=pending_by_path.get(record.path) if reloading else None,
            )
        )
        seen.add(record.path)
    for item in pending:
        if item.path in seen or item.path in disabled_set:
            continue
        entries.append(_entry_from_pending(item))
        seen.add(item.path)
    for path in sorted(disabled_set, key=lambda item: item.as_posix()):
        if path in seen:
            continue
        entries.append(_entry_from_disabled(path))
    entries.sort(key=lambda entry: entry["path"])
    return entries


def _entry_from_record(
    record: Blendlet,
    *,
    reloading: bool,
    pending_hash: str | None,
) -> dict[str, Any]:
    error = _error_payload(record)
    if reloading:
        state = "reloading"
        file_hash = pending_hash
    elif record.error is not None:
        state = "error"
        file_hash = record.attempted_hash
    elif record.persistent or record.module is not None:
        state = "loaded"
        file_hash = record.attempted_hash
    else:
        state = "ran"
        file_hash = record.attempted_hash
    active = record.module is not None
    last_good = bool(record.error is not None and record.persistent and active)
    if record.persistent:
        kind = "persistent"
    elif record.module is not None:
        kind = "module"
    else:
        kind = "transient"
    return {
        "name": record.path.stem,
        "path": str(record.path),
        "state": state,
        "kind": kind,
        "file_hash": file_hash,
        "active_hash": record.source_hash if active else None,
        "generation": record.generation,
        "updated_at": format_timestamp(record.updated_at) if record.updated_at else None,
        "active": active,
        "last_good": last_good,
        "error": error,
    }


def _entry_from_disabled(path: Path) -> dict[str, Any]:
    return {
        "name": path.stem,
        "path": str(path),
        "state": "disabled",
        "kind": None,
        "file_hash": fingerprint_file(path),
        "active_hash": None,
        "generation": 0,
        "updated_at": None,
        "active": False,
        "last_good": False,
        "error": None,
    }


def _entry_from_pending(item: PendingFile) -> dict[str, Any]:
    return {
        "name": item.path.stem,
        "path": str(item.path),
        "state": "reloading",
        "kind": None,
        "file_hash": item.fingerprint,
        "active_hash": None,
        "generation": 0,
        "updated_at": None,
        "active": False,
        "last_good": False,
        "error": None,
    }


def _error_payload(record: Blendlet) -> dict[str, str] | None:
    if record.error is None:
        return None
    return {
        "type": type(record.error).__name__,
        "message": str(record.error),
        "traceback": record.error_traceback or "",
        "file": f"{STATUS_DIRNAME}/{ERRORS_DIRNAME}/{record.path.stem}.txt",
    }


def _sync_error_files(blendlets_directory: Path, entries: list[dict[str, Any]]) -> None:
    errors_dir = status_directory(blendlets_directory) / ERRORS_DIRNAME
    wanted: dict[str, str] = {}
    for entry in entries:
        error = entry.get("error")
        if not error:
            continue
        traceback_text = error.get("traceback") or ""
        if traceback_text:
            wanted[Path(entry["path"]).stem] = traceback_text
    if errors_dir.is_dir():
        for existing in errors_dir.glob("*.txt"):
            if existing.stem not in wanted:
                existing.unlink(missing_ok=True)
    for stem, text in wanted.items():
        body = text if text.endswith("\n") else text + "\n"
        atomic_write_text(errors_dir / f"{stem}.txt", body)
