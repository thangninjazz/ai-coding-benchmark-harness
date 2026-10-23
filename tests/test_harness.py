from pathlib import Path
from harness.evaluator import TaskEvaluator
from harness.models import TestStatus


def test_task_discovery():
    tasks_dir = Path(__file__).resolve().parent.parent / "tasks"
    evaluator = TaskEvaluator(tasks_dir=tasks_dir)
    tasks = evaluator.discover_tasks()
    assert len(tasks) >= 2
    task_ids = [t.task_id for t in tasks]
    assert "task_001_concurrent_lru_cache" in task_ids
    assert "task_002_async_token_bucket_limiter" in task_ids


def test_evaluator_gold_solution_success():
    tasks_dir = Path(__file__).resolve().parent.parent / "tasks"
    evaluator = TaskEvaluator(tasks_dir=tasks_dir)
    tasks = evaluator.discover_tasks()
    lru_task = next(t for t in tasks if t.task_id == "task_001_concurrent_lru_cache")

    result = evaluator.evaluate_task(lru_task, use_gold=True)
    assert result.status == TestStatus.PASSED
    assert result.passed_tests > 0
