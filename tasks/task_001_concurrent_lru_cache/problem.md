# Task 001: Thread-Safe LRU Cache with Millisecond TTL Expiration

## Problem Brief
Design and implement a high-concurrency, thread-safe **Least Recently Used (LRU) Cache** in Python supporting:
1. `get(key: str) -> Optional[Any]`: Retrieves the value for the key if present and not expired; updates key recency.
2. `put(key: str, value: Any, ttl_ms: Optional[int] = None) -> None`: Inserts or updates the key with an optional time-to-live in milliseconds. If capacity is exceeded, evicts the least recently accessed, non-expired key.
3. `size() -> int`: Returns current count of valid (non-expired) entries.

### Strict Non-Functional Requirements:
* **Time Complexity**: Both `get()` and `put()` must run in amortized $\mathcal{O}(1)$ time.
* **Concurrency**: Must maintain linearizability under heavy concurrent read/write access across multiple worker threads without race conditions or memory corruption.
* **Deterministic Eviction**: Eviction must prioritize expired items first, then true LRU order.
