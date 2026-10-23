"""Integration tests for the Docker backend. Skipped unless the image has been built:

    docker build -t ai-benchmark-harness:latest .
"""

import shutil
import subprocess
import time

import pytest

from harness.evaluator import TaskEvaluator
from harness.models import TestStatus
from harness.sandbox import make_sandbox

from .conftest import TASK1, TASK2, TASK3

IMAGE = "ai-benchmark-harness:latest"


def _image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _image_available(), reason=f"docker image {IMAGE} not available"),
]


@pytest.fixture(scope="module")
def docker_evaluator(tasks_dir):
    return TaskEvaluator(tasks_dir, timeout_seconds=60, sandbox=make_sandbox("docker", IMAGE))


def test_gold_resolves_in_container(docker_evaluator, tasks):
    res = docker_evaluator.evaluate_task(tasks[TASK2], use_gold=True)
    assert res.status == TestStatus.RESOLVED, res.error_message


def test_baseline_fails_in_container(docker_evaluator, tasks):
    res = docker_evaluator.evaluate_task(tasks[TASK2])
    assert res.status == TestStatus.FAILED
    assert set(res.failed_test_ids) == set(tasks[TASK2].meta.fail_to_pass)


def test_container_has_no_network_and_a_read_only_submission(docker_evaluator, tasks, monkeypatch):
    monkeypatch.setattr("harness.workspace.scan_code", lambda *a, **k: [])  # reach the sandbox
    gold = tasks[TASK1].solution_file.read_text(encoding="utf-8")
    probe = gold + (
        "\nimport socket\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "    raise SystemExit('network reachable')\nexcept OSError:\n    pass\n"
        "try:\n    open('conftest.py', 'w').write('')\n"
        "    raise SystemExit('submission is writable')\nexcept OSError:\n    pass\n"
    )
    res = docker_evaluator.evaluate_code(tasks[TASK1], probe)
    assert res.status == TestStatus.RESOLVED, res.error_message


def test_hang_is_killed_and_container_removed(tasks_dir, tasks, monkeypatch):
    monkeypatch.setattr("harness.workspace.scan_code", lambda *a, **k: [])
    fast = TaskEvaluator(tasks_dir, timeout_seconds=8, sandbox=make_sandbox("docker", IMAGE))
    res = fast.evaluate_code(tasks[TASK1], "while True:\n    pass\n")
    assert res.status == TestStatus.TIMEOUT
    for _ in range(20):  # --rm removal is asynchronous
        left = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=bench-", "--format", "{{.Names}}"],
            capture_output=True, text=True,
        ).stdout.split()
        if not left:
            break
        time.sleep(0.5)
    assert left == []


def test_patch_task_gold_resolves_and_empty_patch_fails_in_container(docker_evaluator, tasks):
    cart = tasks[TASK3]
    assert docker_evaluator.evaluate_task(cart, use_gold=True).status == TestStatus.RESOLVED
    assert docker_evaluator.evaluate_task(cart).status == TestStatus.FAILED
