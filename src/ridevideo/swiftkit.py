"""Compile-on-demand Swift helpers and their JSON caches.

Three modules each carried a near-identical `_ensure_binary()` and two carried
near-identical cache load/save pairs.  Both patterns live here now.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .paths import BIN_DIR, SWIFT_DIR


def ensure_binary(name: str, frameworks: list[str]) -> Path | None:
    """Compile `swift/<name>.swift` to `bin/<name>bin` once, then reuse it.

    Returns None when swiftc is unavailable or compilation fails, so callers can
    degrade instead of crashing: a missing basemap is recoverable, and route
    inference simply falls back to a straight line.
    """
    binary = BIN_DIR / f"{name}bin"
    if binary.exists():
        return binary
    source = SWIFT_DIR / f"{name}.swift"
    if not source.exists():
        return None
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = BIN_DIR / f".{name}.compile.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.exists():
            return binary
        command = ["swiftc", str(source)]
        for framework in frameworks:
            command += ["-framework", framework]
        temporary = BIN_DIR / f".{name}.{os.getpid()}.tmp"
        command += ["-o", str(temporary)]
        try:
            done = subprocess.run(command, capture_output=True, text=True)
            if done.returncode == 0 and temporary.exists():
                os.replace(temporary, binary)
                return binary
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return None


class JsonCache:
    """A dict persisted to JSON, tolerant of a missing or corrupt file."""

    def __init__(self, path: Path, *, indent: int = 1, sort_keys: bool = False) -> None:
        self.path = path
        self.indent = indent
        self.sort_keys = sort_keys
        self._data: dict[str, Any] | None = None
        self._pending: dict[str, Any] = {}

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            self._data = loaded if isinstance(loaded, dict) else {}
        return self._data

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._pending[key] = value

    def flush(self) -> None:
        if not self._pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    current = {}
                if not isinstance(current, dict):
                    current = {}
                current.update(self._pending)
                fd, temporary_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(current, handle, ensure_ascii=False,
                                  indent=self.indent, sort_keys=self.sort_keys)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
                self._data = current
                self._pending.clear()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
