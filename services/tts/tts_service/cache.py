"""In-memory LRU cache bounded by entry count and total bytes."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable


class LruCache[V]:
    """Least-recently-used cache. ``size_of`` reports each value's byte cost.

    Values larger than ``max_item_bytes`` are never stored, so one long clip
    cannot flush the short repeated phrases the cache exists for. Thread-safe.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        size_of: Callable[[V], int],
        max_item_bytes: int | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_item_bytes = max_bytes if max_item_bytes is None else min(max_item_bytes, max_bytes)
        self._size_of = size_of
        self._items: OrderedDict[Hashable, tuple[V, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> V | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: Hashable, value: V) -> bool:
        """Store ``value``; returns False when it is too large to cache."""
        size = self._size_of(value)
        if self._max_entries <= 0 or size > self._max_item_bytes:
            return False
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._items[key] = (value, size)
            self._bytes += size
            while self._items and (len(self._items) > self._max_entries or self._bytes > self._max_bytes):
                _, (_, evicted) = self._items.popitem(last=False)
                self._bytes -= evicted
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    @property
    def nbytes(self) -> int:
        return self._bytes
