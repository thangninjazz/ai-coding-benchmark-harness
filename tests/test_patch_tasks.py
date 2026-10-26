"""Patch-style (SWE-bench-like) tasks: the candidate is a unified diff against ``repo/``."""

import pytest

from harness.models import TestStatus

from .conftest import TASK3


@pytest.fixture
def cart(tasks):
    return tasks[TASK3]


def _diff(path: str, old: str, new: str, context: str = "") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n-{old}\n+{new}\n"


def test_task_is_loaded_as_a_patch_task(cart):
    assert cart.meta.kind == "patch"
    assert cart.meta.editable_paths == ["shop/"]
    assert cart.baseline_file is None
    assert cart.baseline_candidate() == ""
    assert cart.gold_candidate().startswith("--- a/shop/")


def test_gold_patch_resolves_and_empty_patch_does_not(evaluator, cart):
    gold = evaluator.evaluate_task(cart, use_gold=True)
    assert gold.status == TestStatus.RESOLVED, gold.error_message
    assert gold.passed_tests == gold.total_tests

    before = evaluator.evaluate_task(cart)  # no patch = the buggy snapshot
    assert before.status == TestStatus.FAILED
    assert set(before.failed_test_ids) == set(cart.meta.fail_to_pass)


def test_a_partial_fix_is_not_resolved(evaluator, cart):
    """Fixing only the rounding bug (one of four files) must not be enough."""
    patch = (
        "--- a/shop/money.py\n+++ b/shop/money.py\n@@ -14,3 +14,3 @@\n"
        ' def quantize(amount):\n     """Round a Decimal to whole cents."""\n'
        "-    return amount.quantize(CENT)\n+    return amount.quantize(CENT, rounding=\"ROUND_HALF_UP\")\n"
    )
    res = evaluator.evaluate_code(cart, patch)
    assert res.status == TestStatus.FAILED and not res.resolved
    assert 0 < res.fail_to_pass_passed < res.fail_to_pass_total


def test_model_style_diff_with_wrong_line_numbers_still_applies(evaluator, cart):
    gold = cart.gold_candidate()
    shifted = gold.replace("@@ -1,", "@@ -90,", 1)  # first hunk claims the wrong place
    assert evaluator.evaluate_code(cart, shifted).resolved


@pytest.mark.parametrize(
    "patch,why",
    [
        ("not a diff at all", None),  # no hunks: treated as an empty patch, so unresolved
        (_diff("shop/missing.py", "x", "y"), "does not exist"),
        (_diff("shop/money.py", "this line is not there", "y"), "does not match"),
        (_diff("test_task.py", "x", "y"), "outside the editable"),
        (_diff("pytest.ini", "x", "y"), "only .py"),
        (_diff("shop/conftest.py", "x", "y"), "loaded implicitly"),
        (_diff("../escape.py", "x", "y"), "traversal"),
        (_diff("/etc/passwd", "x", "y"), "illegal path"),
        (_diff("shop/data.txt", "x", "y"), "only .py"),
    ],
)
def test_bad_patches_are_rejected_without_running_tests(evaluator, cart, patch, why):
    res = evaluator.evaluate_code(cart, patch)
    assert not res.resolved
    if why is None:
        assert res.status == TestStatus.FAILED
    else:
        assert res.status == TestStatus.PATCH_ERROR
        assert why in (res.error_message or "")
        assert res.passed_tests == 0


def test_static_scan_runs_on_the_files_the_patch_touches(evaluator, cart):
    sneaky = (
        "--- /dev/null\n+++ b/shop/helper.py\n@@ -0,0 +1,2 @@\n+import os\n+os._exit(0)\n"
    )
    res = evaluator.evaluate_code(cart, sneaky)
    assert res.status == TestStatus.ANTI_CHEAT_VIOLATION
    assert any("shop/helper.py" in v for v in res.anti_cheat_violations)


def test_importing_outside_the_allowlist_in_a_patched_file_is_rejected(evaluator, cart):
    patch = "--- /dev/null\n+++ b/shop/extra.py\n@@ -0,0 +1 @@\n+import json\n"
    res = evaluator.evaluate_code(cart, patch)
    assert res.status == TestStatus.ANTI_CHEAT_VIOLATION
    assert any("allowlist" in v for v in res.anti_cheat_violations)


def test_relative_imports_are_allowed_inside_the_package(evaluator, cart):
    """Real patches often use them; the scan must not reject legitimate package-internal imports."""
    import tempfile
    from pathlib import Path

    from harness.workspace import stage_workspace

    patch = "--- /dev/null\n+++ b/shop/extra.py\n@@ -0,0 +1,2 @@\n+from .money import ZERO\n+VALUE = ZERO\n"
    with tempfile.TemporaryDirectory() as tmp:
        staged = stage_workspace(cart, patch, Path(tmp))
    assert staged.error is None and staged.violations == []


def test_a_patch_that_writes_files_at_import_time_is_caught_as_tampering(evaluator, cart, no_static_scan):
    """With the static scan out of the way, the runtime hash check still sees the new file."""
    gold = cart.gold_candidate()
    gold_plus = gold + (
        "--- a/shop/__init__.py\n+++ b/shop/__init__.py\n@@ -4,3 +4,4 @@\n"
        " from shop.cart import Cart\n from shop.coupons import FixedOff, PercentOff\n \n"
        "+open('dropped.txt', 'w').write('x')\n"
    )
    res = evaluator.evaluate_code(cart, gold_plus)
    assert res.status == TestStatus.ERROR and not res.resolved
    assert "tampering" in (res.error_message or "")
