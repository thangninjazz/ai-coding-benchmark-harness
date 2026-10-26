import difflib
import shutil
import subprocess
from typing import Dict

import pytest

from harness.evaluator import TaskEvaluator
from harness.sandbox import make_sandbox

from ..conftest import TASK1, TASK3

IMAGE = "ai-benchmark-harness:latest"


def is_docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", IMAGE],
                capture_output=True,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def local_evaluator(tasks_dir) -> TaskEvaluator:
    return TaskEvaluator(tasks_dir, timeout_seconds=10, sandbox=make_sandbox("local"))


@pytest.fixture
def docker_evaluator_opt(tasks_dir):
    if not is_docker_available():
        pytest.skip(f"docker or image {IMAGE} not available")
    return TaskEvaluator(
        tasks_dir, timeout_seconds=20, sandbox=make_sandbox("docker", IMAGE)
    )


@pytest.fixture
def target_tasks(tasks) -> Dict:
    return {
        "single": tasks[TASK1],
        "patch": tasks[TASK3],
    }


def make_patch_cheat(task, injected_code: str, target_file: str = "shop/allocation.py") -> str:
    """Generate a valid unified diff injecting adversarial code into a patch task."""
    orig = (task.path / "repo" / target_file).read_text(encoding="utf-8")
    new = injected_code.strip() + "\n\n" + orig
    diff = difflib.unified_diff(
        orig.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{target_file}",
        tofile=f"b/{target_file}",
    )
    return "".join(diff)
