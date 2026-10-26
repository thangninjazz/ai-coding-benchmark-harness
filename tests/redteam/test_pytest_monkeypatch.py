"""Red-team tests: monkeypatching pytest internals.

A candidate monkeypatches pytest's report generation (`_pytest.reports.TestReport.from_item_and_call`)
to rewrite every non-canary test outcome to 'passed'. Because the canary test is preserved as
'failed', the canary integrity check is fooled and the harness reports RESOLVED for a completely
buggy candidate.
"""

import pytest

from harness.models import TestStatus

from .conftest import make_patch_cheat

MONKEYPATCH_SNIPPET = """
import _pytest.reports
_orig_from_item_and_call = _pytest.reports.TestReport.from_item_and_call

def _adversarial_report(item, call, **kw):
    rep = _orig_from_item_and_call(item, call, **kw)
    if "canary" not in str(getattr(item, "nodeid", "")):
        rep.outcome = "passed"
    return rep

_pytest.reports.TestReport.from_item_and_call = _adversarial_report
"""


@pytest.mark.xfail(
    reason="Vulnerability in current architecture (Section 1): pytest in-process monkeypatching",
    strict=True,
)
def test_monkeypatch_testreport_single_file(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat_code = MONKEYPATCH_SNIPPET + "\n" + task.baseline_candidate()
    res = local_evaluator.evaluate_code(task, cheat_code)
    # Under vulnerability, res.resolved is True. Mitigated, it must be False.
    assert not res.resolved
    assert res.status != TestStatus.RESOLVED


@pytest.mark.xfail(
    reason=(
        "Vulnerability in current architecture (Section 1): "
        "pytest in-process monkeypatching in patch task"
    ),
    strict=True,
)
def test_monkeypatch_testreport_patch_task(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["patch"]
    patch = make_patch_cheat(task, MONKEYPATCH_SNIPPET)
    res = local_evaluator.evaluate_code(task, patch)
    assert not res.resolved
    assert res.status != TestStatus.RESOLVED
