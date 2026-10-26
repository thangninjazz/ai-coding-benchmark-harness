"""Red-team tests: resource exhaustion attacks.

Submissions must not be able to exhaust CPU, threads/processes, memory, disk, or output buffers.
All must be constrained or caught and marked un-resolved (TIMEOUT or ERROR).
"""


from harness.models import TestStatus


def test_infinite_loop_timeout(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
while True:
    pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.TIMEOUT


def test_infinite_sleep_timeout(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import time
time.sleep(3600)
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.TIMEOUT


def test_huge_stdout_output(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import sys
# Generate 10MB of output
chunk = "A" * 1024 + "\\n"
for _ in range(10 * 1024):
    sys.stdout.write(chunk)
sys.stdout.flush()

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
    def get(self, k): return None
    def put(self, k, v, ttl=None): pass
    def size(self): return 0
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    # stdout output must be capped (not crashing memory)
    assert len(res.stdout) <= 25_000


def test_disk_filling_attempt(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
try:
    with open("bigfile.bin", "wb") as f:
        f.write(b"0" * (50 * 1024 * 1024))
except OSError:
    pass

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
    def get(self, k): return None
    def put(self, k, v, ttl=None): pass
    def size(self): return 0
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    # Tampering or quota violation
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


def test_thread_bomb(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import threading, time

def worker():
    while True:
        time.sleep(1)

for _ in range(500):
    try:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    except RuntimeError:
        break
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
