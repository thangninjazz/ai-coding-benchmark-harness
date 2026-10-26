"""Red-team tests: workspace and filesystem tampering.

Submissions must not be able to create hook files (conftest.py, sitecustomize.py,
usercustomize.py, *.pth) or alter test_task.py, pytest.ini, or canary files.
Any modification or creation of unexpected files must trigger workspace tampering detection.
"""


from harness.models import TestStatus


def test_create_conftest_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("conftest.py", "w") as f:
    f.write("# hook")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_create_sitecustomize_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("sitecustomize.py", "w") as f:
    f.write("# hook")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_create_usercustomize_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("usercustomize.py", "w") as f:
    f.write("# hook")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_create_pth_file_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("exploit.pth", "w") as f:
    f.write("import sys\\n")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_modify_test_task_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("test_task.py", "a") as f:
    f.write("\\n# appended\\n")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_modify_pytest_ini_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("pytest.ini", "w") as f:
    f.write("[pytest]\\n")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR
    assert "workspace tampering" in (res.error_message or "")


def test_modify_canary_tampering(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
with open("_harness_canary.py", "w") as f:
    f.write("def test_canary_must_fail(): pass\\n")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    assert res.status == TestStatus.ERROR


def test_patch_path_traversal_rejected(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["patch"]
    traversal_patch = """--- a/../secret.py
+++ b/../secret.py
@@ -0,0 +1,1 @@
+exploit = 1
"""
    res = local_evaluator.evaluate_code(task, traversal_patch)
    assert not res.resolved
    assert res.status == TestStatus.PATCH_ERROR


def test_patch_forbidden_file_rejected(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["patch"]
    forbidden_patch = """--- a/conftest.py
+++ b/conftest.py
@@ -0,0 +1,1 @@
+hook = 1
"""
    res = local_evaluator.evaluate_code(task, forbidden_patch)
    assert not res.resolved
    assert res.status == TestStatus.PATCH_ERROR
