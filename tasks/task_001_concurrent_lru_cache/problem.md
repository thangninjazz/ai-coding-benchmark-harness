# Task 001: Thread-Safe LRU Cache with Millisecond TTL

Implement `ConcurrentLRUCache` in a single module (`implementation.py`).

## Interface

```python
class ConcurrentLRUCache:
    def __init__(self, capacity: int): ...
    def get(self, key: str) -> Optional[Any]: ...
    def put(self, key: str, value: Any, ttl_ms: Optional[int] = None) -> None: ...
    def size(self) -> int: ...
```

## Specification

**Construction**
* `capacity <= 0` raises `ValueError`.

**Time**
* Read time *only* through `time.monotonic()`, called as an attribute of the `time`
  module at the moment it is needed (never `time.time()`, never a cached reference).
  An entry written at time `t0` with `ttl_ms` is **valid iff** `now < t0 + ttl_ms / 1000`.
  In particular `ttl_ms=0` is already expired when written, and an entry is expired
  *at* its deadline instant.
* `ttl_ms=None` means the entry never expires. `ttl_ms < 0` raises `ValueError`.

**`put(key, value, ttl_ms=None)`**
* Inserts or replaces the entry. Replacing replaces **both** value and TTL: a later
  `put` without `ttl_ms` makes the entry non-expiring, and a later `put` with `ttl_ms`
  restarts the deadline from the time of that `put`.
* The written key becomes the most recently used.
* Any value may be stored, including falsy ones (`0`, `""`, `False`, `[]`).
* If the number of stored entries now exceeds `capacity`: first discard **all expired
  entries**; only if the cache is still over capacity, evict least-recently-used
  entries until it fits. A live entry must never be evicted while an expired one is
  still stored.

**`get(key)`**
* Returns the value if the key is present and valid, and marks it most recently used.
* Returns `None` if the key is absent or expired (an expired entry is removed).

**`size()`**
* Returns the number of **valid** (non-expired) entries.

## Non-functional requirements

* **Thread safety.** All methods are safe to call concurrently from many threads.
  Under contention: `size()` never exceeds `capacity`, `get(key)` never returns a value
  stored under a different key, and no operation raises.
* **Complexity.** `get` is amortized `O(1)`; `put` and `size` are amortized
  `O(log n)`. A solution that scans every entry on each call does not meet this.
* Standard library only.
