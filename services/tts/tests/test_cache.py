from __future__ import annotations

from tts_service.cache import LruCache


def make(max_entries: int = 3, max_bytes: int = 100, max_item_bytes: int | None = None) -> LruCache[bytes]:
    return LruCache(max_entries=max_entries, max_bytes=max_bytes, size_of=len, max_item_bytes=max_item_bytes)


def test_get_put_and_recency() -> None:
    cache = make()
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.put("c", b"3")
    assert cache.get("a") == b"1"  # refreshes "a"
    cache.put("d", b"4")  # evicts the least recently used: "b"
    assert "b" not in cache
    assert [cache.get(k) for k in ("a", "c", "d")] == [b"1", b"3", b"4"]
    assert cache.get("missing") is None


def test_byte_budget_evicts_oldest() -> None:
    cache = make(max_entries=10, max_bytes=10)
    cache.put("a", b"xxxx")
    cache.put("b", b"yyyy")
    assert cache.nbytes == 8
    cache.put("c", b"zzzz")  # 12 > 10: drop "a"
    assert "a" not in cache and len(cache) == 2 and cache.nbytes == 8


def test_replacing_a_key_updates_bytes() -> None:
    cache = make(max_bytes=10)
    cache.put("a", b"xxxx")
    cache.put("a", b"xx")
    assert len(cache) == 1 and cache.nbytes == 2 and cache.get("a") == b"xx"


def test_oversized_items_are_not_cached() -> None:
    cache = make(max_entries=10, max_bytes=100, max_item_bytes=5)
    cache.put("small", b"abc")
    assert cache.put("big", b"0123456789") is False
    assert "big" not in cache and cache.get("small") == b"abc"


def test_disabled_cache() -> None:
    cache = make(max_entries=0)
    assert cache.put("a", b"1") is False
    assert len(cache) == 0


def test_clear() -> None:
    cache = make()
    cache.put("a", b"1")
    cache.clear()
    assert len(cache) == 0 and cache.nbytes == 0
