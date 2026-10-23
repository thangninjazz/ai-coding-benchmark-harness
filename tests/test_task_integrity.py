"""Meta-tests: every shipped task must itself be a *valid* benchmark."""

import pytest

from harness.verify import apply_mutant, collect_test_ids, verify_task


def test_declared_tests_match_the_test_file_exactly(tasks):
    for task in tasks.values():
        collected = collect_test_ids(task)
        declared = task.meta.fail_to_pass + task.meta.pass_to_pass
        assert sorted(collected) == sorted(declared), task.task_id
        assert len(set(declared)) == len(declared), "a test is listed twice"
        assert task.meta.fail_to_pass, "a task needs at least one FAIL_TO_PASS test"


def test_every_task_ships_a_meaningful_mutant_suite(tasks):
    for task in tasks.values():
        gold = task.solution_file.read_text(encoding="utf-8")
        assert len(task.meta.mutants) >= 12, task.task_id
        names = [m.name for m in task.meta.mutants]
        assert len(set(names)) == len(names)
        for mutant in task.meta.mutants:
            assert mutant.reason
            assert apply_mutant(gold, mutant) != gold


def test_apply_mutant_rejects_ambiguous_or_noop_edits(tasks):
    from harness.models import Edit, Mutant

    gold = next(iter(tasks.values())).solution_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="expected 1 occurrence"):
        apply_mutant(gold, Mutant(name="m", reason="r", edits=[Edit(find="self", replace="x")]))
    with pytest.raises(ValueError, match="does not change"):
        noop = Edit(find="import time", replace="import time")
        apply_mutant(gold, Mutant(name="m", reason="r", edits=[noop]))


@pytest.mark.slow
def test_each_task_is_valid(evaluator, tasks):
    """gold resolves, baseline fails exactly where declared, every mutant is killed."""
    for task in tasks.values():
        report = verify_task(evaluator, task, repeat=1)
        failed = [f"{c.name}: {c.detail}" for c in report.checks if not c.ok]
        assert report.ok, f"{task.task_id}: {failed}"
        assert report.mutation_score == 1.0
