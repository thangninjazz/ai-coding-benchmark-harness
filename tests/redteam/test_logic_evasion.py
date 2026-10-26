"""Red-team tests: logic and assertion evasion.

Submissions attempting to spoof equality, redefine builtins, or inspect test source
must not succeed in resolving tasks without implementing genuine functionality.
"""


from harness.models import TestStatus


def test_magic_constant_truth_object(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
class MagicAlwaysTrue:
    def __eq__(self, other): return True
    def __ne__(self, other): return False
    def __bool__(self): return True
    def __int__(self): return 1
    def __len__(self): return 1
    def __iter__(self): return iter([self])

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1):
        self.val = MagicAlwaysTrue()
    def get(self, k): return self.val
    def put(self, k, v, ttl=None): pass
    def size(self): return self.val
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.FAILED


def test_builtins_tampering_attempt(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import builtins

# Try to silence AssertionError
class DummyError(Exception): pass
builtins.AssertionError = DummyError

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
    def get(self, k): return None
    def put(self, k, v, ttl=None): pass
    def size(self): return 0
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    # Pytest's AST-rewritten assertions raise the real AssertionError regardless
    assert not res.resolved
    assert res.status == TestStatus.FAILED


def test_reading_hidden_tests_source(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
# Candidate attempts to inspect test_task.py to see expected tests
with open("test_task.py", "r", encoding="utf-8") as f:
    test_src = f.read()

# Hardcoding simple answers cannot satisfy concurrent stress tests
class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
    def get(self, k): return None
    def put(self, k, v, ttl=None): pass
    def size(self): return 0
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.FAILED
