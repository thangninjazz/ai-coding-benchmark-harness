"""The evaluator must never report RESOLVED unless the real tests really passed."""

import pytest

from harness.evaluator import TaskEvaluator, TaskLoadError, parse_junit
from harness.models import TestStatus

from .conftest import TASK1

JUNIT_HEADER = '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
JUNIT_FOOTER = "</testsuite></testsuites>"


@pytest.fixture
def lru(tasks):
    return tasks[TASK1]


@pytest.fixture
def gold(lru):
    return lru.solution_file.read_text(encoding="utf-8")


def test_gold_is_resolved_for_every_task(evaluator, tasks):
    for task in tasks.values():
        res = evaluator.evaluate_task(task, use_gold=True)
        assert res.status == TestStatus.RESOLVED, (task.task_id, res.error_message)
        assert res.resolved
        assert res.passed_tests == res.total_tests == len(task.meta.expected_tests)
        assert res.fail_to_pass_passed == res.fail_to_pass_total
        assert res.pass_to_pass_passed == res.pass_to_pass_total


def test_baseline_is_unresolved_and_fails_exactly_the_fail_to_pass_tests(evaluator, tasks):
    for task in tasks.values():
        res = evaluator.evaluate_task(task)
        assert res.status == TestStatus.FAILED, (task.task_id, res.error_message)
        assert not res.resolved
        assert set(res.failed_test_ids) == set(task.meta.fail_to_pass)
        assert res.pass_to_pass_passed == res.pass_to_pass_total


def test_empty_candidate_is_not_silently_replaced_by_the_baseline(evaluator, lru):
    res = evaluator.evaluate_task(lru, candidate_code="")
    assert not res.resolved
    assert res.status in (TestStatus.ERROR, TestStatus.FAILED)


def test_anti_cheat_violation_short_circuits(evaluator, lru):
    res = evaluator.evaluate_task(lru, candidate_code="import pytest\n")
    assert res.status == TestStatus.ANTI_CHEAT_VIOLATION
    assert res.anti_cheat_violations and not res.resolved


def test_allowlist_is_enforced_per_task(evaluator, lru):
    res = evaluator.evaluate_task(lru, candidate_code="import json\n")
    assert res.status == TestStatus.ANTI_CHEAT_VIOLATION
    assert "allowlist" in res.anti_cheat_violations[0]


def test_early_exit_with_status_zero_cannot_fake_a_pass(evaluator, lru, no_static_scan):
    res = evaluator.evaluate_task(lru, candidate_code="import os\nos._exit(0)\n")
    assert res.status == TestStatus.ERROR
    assert not res.resolved and res.passed_tests == 0
    assert "No JUnit report" in (res.error_message or "")


def test_import_time_crash_is_not_resolved(evaluator, lru, no_static_scan):
    res = evaluator.evaluate_task(lru, candidate_code="raise RuntimeError('boom')\n")
    assert not res.resolved
    assert res.status == TestStatus.ERROR


def test_infinite_loop_times_out(tasks_dir, lru, no_static_scan):
    fast = TaskEvaluator(tasks_dir, timeout_seconds=4)
    res = fast.evaluate_task(lru, candidate_code="while True:\n    pass\n")
    assert res.status == TestStatus.TIMEOUT
    assert not res.resolved


def test_creating_conftest_is_detected_as_tampering(evaluator, lru, gold, no_static_scan):
    res = evaluator.evaluate_task(lru, candidate_code=gold + "\nopen('conftest.py', 'w').write('')\n")
    assert res.status == TestStatus.ERROR and not res.resolved
    assert "tampering" in (res.error_message or "")


def test_editing_the_test_file_is_detected_as_tampering(evaluator, lru, gold, no_static_scan):
    cheat = gold + "\nopen('test_task.py', 'a').write('\\n# patched')\n"
    res = evaluator.evaluate_task(lru, candidate_code=cheat)
    assert res.status == TestStatus.ERROR and not res.resolved
    assert "test_task.py" in (res.error_message or "")


def test_parent_environment_secrets_are_invisible(evaluator, lru, gold, no_static_scan, monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_SECRET", "hunter2")
    probe = gold + "\nimport os\nif os.environ.get('HARNESS_TEST_SECRET'):\n    raise RuntimeError('leak')\n"
    res = evaluator.evaluate_task(lru, candidate_code=probe)
    assert res.status == TestStatus.RESOLVED, res.error_message


def test_missing_or_invalid_task_files_raise(tmp_path, evaluator):
    broken = tmp_path / "task_x"
    broken.mkdir()
    with pytest.raises(TaskLoadError, match="missing files"):
        evaluator.load_task(broken)
    for name in ("problem.md", "baseline.py", "solution.py", "test_task.py"):
        (broken / name).write_text("")
    (broken / "task.json").write_text('{"title": "x"}')
    with pytest.raises(TaskLoadError, match="invalid task.json"):
        evaluator.load_task(broken)


def test_parse_junit_classifies_outcomes(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text(
        JUNIT_HEADER
        + '<testcase classname="test_task" name="ok"/>'
        + '<testcase classname="test_task" name="bad"><failure message="x"/></testcase>'
        + '<testcase classname="test_task" name="boom"><error message="x"/></testcase>'
        + '<testcase classname="test_task" name="skip"><skipped message="x"/></testcase>'
        + '<testcase classname="test_task.TestGroup" name="nested"/>'
        + JUNIT_FOOTER
    )
    assert parse_junit(report) == {
        "ok": "passed", "bad": "failed", "boom": "failed", "skip": "skipped",
        "TestGroup.nested": "passed",
    }


def test_parse_junit_rejects_garbage(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text("not xml")
    with pytest.raises(Exception):  # noqa: B017 - ParseError type is an implementation detail
        parse_junit(report)
