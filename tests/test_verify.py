"""Exercise verify/calibrate on a tiny synthetic task so the machinery is tested quickly."""

import json

import pytest

from harness.cli import main
from harness.evaluator import TaskEvaluator
from harness.verify import calibrate, verify_task

TESTS = """
from implementation import add


def test_adds_positive():
    assert add(2, 3) == 5


def test_adds_negative():
    assert add(-2, -3) == -5


def test_identity():
    assert add(7, 0) == 7
"""

META = {
    "title": "Add",
    "difficulty": "Easy",
    "allowed_imports": [],
    "fail_to_pass": ["test_adds_positive", "test_adds_negative"],
    "pass_to_pass": ["test_identity"],
    "mutants": [
        {"name": "subtract", "reason": "wrong operator",
         "edits": [{"find": "a + b", "replace": "a - b"}]},
    ],
}


def make_task(root, meta=None):
    task = root / "task_900_add"
    task.mkdir(parents=True)
    (task / "problem.md").write_text("Implement add(a, b).")
    (task / "baseline.py").write_text("def add(a, b):\n    return a - b\n")
    (task / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    (task / "test_task.py").write_text(TESTS)
    (task / "task.json").write_text(json.dumps(meta or META))
    return task


@pytest.fixture
def mini(tmp_path):
    make_task(tmp_path)
    evaluator = TaskEvaluator(tmp_path)
    return tmp_path, evaluator, evaluator.discover_tasks()[0]


def test_valid_task_passes_verification(mini):
    _, evaluator, task = mini
    report = verify_task(evaluator, task)
    assert report.ok, [c for c in report.checks if not c.ok]
    assert (report.mutants_killed, report.mutants_total) == (1, 1)


def test_surviving_mutant_makes_the_task_invalid(tmp_path):
    meta = json.loads(json.dumps(META))
    # swapping operands is semantically identical, so no test can ever kill it
    meta["mutants"] = [{"name": "equivalent", "reason": "r",
                        "edits": [{"find": "a + b", "replace": "b + a"}]}]
    make_task(tmp_path, meta)
    evaluator = TaskEvaluator(tmp_path)
    report = verify_task(evaluator, evaluator.discover_tasks()[0])
    assert not report.ok and report.mutation_score == 0.0
    assert any("survived" in c.detail for c in report.checks if not c.ok)


def test_wrong_fail_to_pass_declaration_is_caught(tmp_path):
    meta = json.loads(json.dumps(META))
    meta["fail_to_pass"] = ["test_adds_positive", "test_identity"]  # identity passes on baseline
    meta["pass_to_pass"] = ["test_adds_negative"]
    make_task(tmp_path, meta)
    evaluator = TaskEvaluator(tmp_path)
    report = verify_task(evaluator, evaluator.discover_tasks()[0])
    failed = {c.name for c in report.checks if not c.ok}
    assert "every FAIL_TO_PASS test fails on baseline in every run" in failed
    assert "every PASS_TO_PASS test passes on baseline" in failed


def test_malformed_mutant_is_reported_not_raised(tmp_path):
    meta = json.loads(json.dumps(META))
    meta["mutants"] = [{"name": "ghost", "reason": "r",
                        "edits": [{"find": "not in the solution", "replace": "x"}]}]
    make_task(tmp_path, meta)
    evaluator = TaskEvaluator(tmp_path)
    report = verify_task(evaluator, evaluator.discover_tasks()[0])
    assert not report.ok
    assert any(c.name.startswith("mutant is well-formed") for c in report.checks)


def test_calibrate_derives_fail_to_pass_and_pass_to_pass(mini):
    _, evaluator, task = mini
    cal = calibrate(evaluator, task, repeat=2)
    assert sorted(cal.fail_to_pass) == ["test_adds_negative", "test_adds_positive"]
    assert cal.pass_to_pass == ["test_identity"]
    assert cal.flaky == []


def test_cli_verify_and_calibrate(mini, capsys):
    root, _, _ = mini
    assert main(["--tasks-dir", str(root), "verify"]) == 0
    assert "VALID" in capsys.readouterr().out

    task_json = root / "task_900_add" / "task.json"
    data = json.loads(task_json.read_text())
    data["fail_to_pass"], data["pass_to_pass"] = [], ["test_adds_positive", "test_adds_negative", "test_identity"]
    task_json.write_text(json.dumps(data))
    assert main(["--tasks-dir", str(root), "calibrate", "task_900_add", "--repeat", "2", "--write"]) == 0
    written = json.loads(task_json.read_text())
    assert sorted(written["fail_to_pass"]) == ["test_adds_negative", "test_adds_positive"]
    assert main(["--tasks-dir", str(root), "verify"]) == 0


def test_cli_verify_exits_nonzero_for_an_invalid_task(tmp_path, capsys):
    meta = json.loads(json.dumps(META))
    meta["mutants"] = [{"name": "equivalent", "reason": "r",
                        "edits": [{"find": "a + b", "replace": "b + a"}]}]
    make_task(tmp_path, meta)
    assert main(["--tasks-dir", str(tmp_path), "verify"]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_cli_reports_task_load_errors(tmp_path, capsys):
    (tmp_path / "task_bad").mkdir()
    assert main(["--tasks-dir", str(tmp_path), "list"]) == 2
    assert "missing files" in capsys.readouterr().out
