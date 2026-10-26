"""Red-team tests: process lifecycle manipulation and exit hijacks.

Submissions must not be able to force a passing verdict by terminating early, registering
atexit handlers, or sending signals to themselves or the parent supervisor.
All of these must result in an un-resolved status (typically ERROR).
"""

import pytest

from harness.models import TestStatus

from .conftest import make_patch_cheat


@pytest.mark.parametrize("exit_code", [0, 1, 2, 42, 137])
def test_os_exit_cannot_resolve(local_evaluator, target_tasks, no_static_scan, exit_code):
    task = target_tasks["single"]
    cheat = f"import os\nos._exit({exit_code})\n"
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


def test_os_exit_in_patch_task(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["patch"]
    cheat = make_patch_cheat(task, "import os\nos._exit(0)")
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_sys_exit_cannot_resolve(local_evaluator, target_tasks, no_static_scan, exit_code):
    task = target_tasks["single"]
    cheat = f"import sys\nsys.exit({exit_code})\n"
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


def test_atexit_handler_cannot_corrupt_verdict(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import atexit, os, sys

def wipe():
    for f in os.listdir('.'):
        try:
            os.remove(f)
        except OSError:
            pass

atexit.register(wipe)

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
    def get(self, k): return None
    def put(self, k, v, ttl=None): pass
    def size(self): return 0
    def clear(self): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved


def test_signal_self_sigterm(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import os, signal
os.kill(os.getpid(), signal.SIGTERM)
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


def test_signal_parent_ignored_or_fails(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    # Attempt to send SIGTERM or SIGKILL to parent process
    cheat = """
import os, signal
try:
    ppid = os.getppid()
    os.kill(ppid, signal.SIGTERM)
except Exception:
    pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
