"""Per-run evidence: what the agent has seen so far (user messages, tool results)."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable
from typing import Any


class Evidence:
    def __init__(self, initial: dict[str, list[Any]] | None = None):
        self._data: dict[str, list[Any]] = {key: list(values) for key, values in (initial or {}).items()}
        self._lock = threading.Lock()

    def record(self, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(key, []).append(value)

    def view(self, keys: Iterable[str]) -> dict[str, list[Any]]:
        """A copy of just the keys a judge reads, safe to hand to another thread."""
        with self._lock:
            return {key: list(self._data.get(key, [])) for key in keys}

    def fingerprint(self, keys: Iterable[str]) -> str:
        blob = json.dumps(self.view(keys), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
