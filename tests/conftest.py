from pathlib import Path

import pytest

from harness.evaluator import TaskEvaluator

ROOT = Path(__file__).resolve().parent.parent
TASK1 = "task_001_concurrent_lru_cache"
TASK2 = "task_002_async_token_bucket_limiter"


@pytest.fixture(scope="session")
def tasks_dir() -> Path:
    return ROOT / "tasks"


@pytest.fixture(scope="session")
def evaluator(tasks_dir) -> TaskEvaluator:
    return TaskEvaluator(tasks_dir)


@pytest.fixture(scope="session")
def tasks(evaluator):
    return {t.task_id: t for t in evaluator.discover_tasks()}


@pytest.fixture
def no_static_scan(monkeypatch):
    """Disable the AST layer so tests can exercise the *runtime* integrity checks."""
    monkeypatch.setattr("harness.evaluator.scan_code", lambda *a, **k: [])
