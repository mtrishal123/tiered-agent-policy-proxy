"""Tier 1: reuse judge verdicts for calls the judge has already seen.

Keys come from the engine and include the policy-set version, the policy, the
canonical call, and a fingerprint of the evidence the judge read. A change to
any of them is a different key, so a verdict is never reused against evidence
it didn't see.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from policyproxy.types import Verdict


@dataclass(frozen=True)
class CachedVerdict:
    verdict: Verdict
    reason: str
    expires_at: float


class DecisionCache:
    def __init__(self, max_entries: int = 10_000, ttl_s: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self._clock = clock
        self._entries: OrderedDict[str, CachedVerdict] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> CachedVerdict | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= self._clock():
                if entry is not None:
                    del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, key: str, verdict: Verdict, reason: str) -> None:
        with self._lock:
            self._entries[key] = CachedVerdict(verdict, reason, self._clock() + self.ttl_s)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
