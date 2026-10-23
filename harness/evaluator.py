"""
Core Evaluation Engine
Orchestrates isolated execution of benchmark tasks against test suites.
"""

import os
import sys
import time
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional

from harness.models import TaskDefinition, BenchmarkResult, TestStatus
from harness.anti_cheat import verify_code_integrity


class TaskEvaluator:
    def __init__(self, tasks_dir: Path, timeout_seconds: int = 20):
        self.tasks_dir = tasks_dir
        self.timeout_seconds = timeout_seconds

    def discover_tasks(self) -> List[TaskDefinition]:
        """Discovers all task definition directories within tasks_dir."""
        tasks = []
        if not self.tasks_dir.exists():
            return tasks

        for item in sorted(self.tasks_dir.iterdir()):
            if item.is_dir() and item.name.startswith("task_"):
                problem_file = item / "problem.md"
                baseline_file = item / "baseline.py"
                test_file = item / "test_task.py"
                gold_patch = item / "solution.patch"

                if baseline_file.exists() and test_file.exists():
                    tasks.append(
                        TaskDefinition(
                            task_id=item.name,
                            title=item.name.replace("_", " ").title(),
                            difficulty="Medium/Hard",
                            language="Python",
                            path=item,
                            problem_statement=problem_file.read_text(encoding="utf-8") if problem_file.exists() else "",
                            baseline_file=baseline_file,
                            test_file=test_file,
                            gold_patch=gold_patch if gold_patch.exists() else None,
                        )
                    )
        return tasks

    def evaluate_task(
        self,
        task: TaskDefinition,
        candidate_code: Optional[str] = None,
        use_gold: bool = False,
    ) -> BenchmarkResult:
        """
        Executes a task inside an isolated temporary directory.
        """
        start_time = time.time()

        # Determine code under test
        if use_gold:
            solution_file = task.path / "solution.py"
            if solution_file.exists():
                code_to_test = solution_file.read_text(encoding="utf-8")
            else:
                code_to_test = task.baseline_file.read_text(encoding="utf-8")
        elif candidate_code:
            code_to_test = candidate_code
        else:
            code_to_test = task.baseline_file.read_text(encoding="utf-8")

        # Static AST Anti-Cheat Check
        violations = verify_code_integrity(code_to_test)
        if violations:
            return BenchmarkResult(
                task_id=task.task_id,
                status=TestStatus.ANTI_CHEAT_VIOLATION,
                duration_seconds=time.time() - start_time,
                anti_cheat_violations=violations,
                error_message="Submission rejected by static anti-cheat checks.",
            )

        # Run in isolated sandbox directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target_impl = temp_path / "implementation.py"
            target_test = temp_path / "test_task.py"

            target_impl.write_text(code_to_test, encoding="utf-8")
            shutil.copy(task.test_file, target_test)

            cmd = [
                sys.executable,
                "-m",
                "pytest",
                str(target_test),
                "-v",
                "--tb=short",
                "-o",
                "pythonpath=.",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(temp_path),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                duration = time.time() - start_time

                stdout = proc.stdout
                stderr = proc.stderr

                if proc.returncode == 0:
                    status = TestStatus.PASSED
                else:
                    status = TestStatus.FAILED

                passed_count = stdout.count(" PASSED")
                failed_count = stdout.count(" FAILED")
                total = passed_count + failed_count

                return BenchmarkResult(
                    task_id=task.task_id,
                    status=status,
                    passed_tests=passed_count,
                    total_tests=total if total > 0 else 1,
                    duration_seconds=duration,
                    stdout=stdout,
                    stderr=stderr,
                    error_message=None if status == TestStatus.PASSED else "Unit test assertions failed.",
                )

            except subprocess.TimeoutExpired:
                return BenchmarkResult(
                    task_id=task.task_id,
                    status=TestStatus.TIMEOUT,
                    duration_seconds=self.timeout_seconds,
                    error_message=f"Execution timed out after {self.timeout_seconds}s (potential infinite loop).",
                )
            except Exception as e:
                return BenchmarkResult(
                    task_id=task.task_id,
                    status=TestStatus.ERROR,
                    duration_seconds=time.time() - start_time,
                    error_message=str(e),
                )
