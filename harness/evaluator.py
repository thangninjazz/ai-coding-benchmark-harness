"""
Core evaluation engine.

Pipeline for one candidate:

1. static anti-cheat scan (per-task import allowlist);
2. materialise ``implementation.py`` + the task's pristine ``test_task.py`` in a fresh
   work directory and run pytest through a sandbox backend;
3. validate the JUnit report: it must exist, parse, and contain *exactly* the expected
   test ids; the work directory must be untouched;
4. score SWE-bench style: a candidate is **resolved** only if every FAIL_TO_PASS and
   PASS_TO_PASS test passed.
"""

import hashlib
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import ValidationError

from harness.anti_cheat import scan_code
from harness.models import BenchmarkResult, TaskDefinition, TaskMeta, TestStatus
from harness.sandbox import JUNIT_NAME, PYTEST_INI, Sandbox, make_sandbox

MAX_REPORT_BYTES = 5 * 1024 * 1024
_WORKDIR_FILES = {"implementation.py", "test_task.py", "pytest.ini"}


class TaskLoadError(Exception):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_junit(report: Path) -> Dict[str, str]:
    """Return ``{test_id: 'passed'|'failed'|'skipped'}`` from a pytest JUnit report."""
    if report.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("JUnit report is implausibly large")
    root = ET.parse(report).getroot()
    outcomes: Dict[str, str] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        if classname == "test_task":
            test_id = name
        elif classname.startswith("test_task."):
            test_id = f"{classname[len('test_task.'):]}.{name}"
        else:
            test_id = f"{classname}.{name}" if classname else name
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        if outcomes.get(test_id) == "failed":
            continue
        outcomes[test_id] = outcome
    return outcomes


class TaskEvaluator:
    def __init__(
        self,
        tasks_dir: Path,
        timeout_seconds: Optional[int] = None,
        sandbox: Optional[Sandbox] = None,
    ):
        self.tasks_dir = Path(tasks_dir)
        self.timeout_override = timeout_seconds
        self.sandbox = sandbox or make_sandbox("local")

    # ------------------------------------------------------------------ discovery
    def load_task(self, directory: Path) -> TaskDefinition:
        required = ["task.json", "problem.md", "baseline.py", "solution.py", "test_task.py"]
        missing = [f for f in required if not (directory / f).is_file()]
        if missing:
            raise TaskLoadError(f"{directory.name}: missing files {missing}")
        try:
            meta = TaskMeta.model_validate_json((directory / "task.json").read_text(encoding="utf-8"))
        except ValidationError as e:
            raise TaskLoadError(f"{directory.name}: invalid task.json\n{e}") from e
        return TaskDefinition(
            task_id=directory.name,
            meta=meta,
            path=directory,
            problem_statement=(directory / "problem.md").read_text(encoding="utf-8"),
            baseline_file=directory / "baseline.py",
            solution_file=directory / "solution.py",
            test_file=directory / "test_task.py",
        )

    def discover_tasks(self) -> List[TaskDefinition]:
        if not self.tasks_dir.exists():
            return []
        return [
            self.load_task(item)
            for item in sorted(self.tasks_dir.iterdir())
            if item.is_dir() and item.name.startswith("task_")
        ]

    # ----------------------------------------------------------------- evaluation
    def evaluate_task(
        self,
        task: TaskDefinition,
        candidate_code: Optional[str] = None,
        use_gold: bool = False,
    ) -> BenchmarkResult:
        """Evaluate gold (``use_gold``), a candidate, or - by default - the buggy baseline."""
        if use_gold:
            code = task.solution_file.read_text(encoding="utf-8")
        elif candidate_code is not None:
            code = candidate_code
        else:
            code = task.baseline_file.read_text(encoding="utf-8")
        return self.evaluate_code(task, code)

    def evaluate_code(self, task: TaskDefinition, code: str) -> BenchmarkResult:
        start = time.perf_counter()

        def result(status: TestStatus, **kw) -> BenchmarkResult:
            return BenchmarkResult(
                task_id=task.task_id,
                status=status,
                duration_seconds=time.perf_counter() - start,
                fail_to_pass_total=len(task.meta.fail_to_pass),
                pass_to_pass_total=len(task.meta.pass_to_pass),
                total_tests=len(task.meta.expected_tests),
                **kw,
            )

        violations = [str(v) for v in scan_code(code, task.meta.allowed_imports)]
        if violations:
            return result(
                TestStatus.ANTI_CHEAT_VIOLATION,
                anti_cheat_violations=violations,
                error_message="Submission rejected by static anti-cheat checks.",
            )

        timeout = self.timeout_override or task.meta.timeout_seconds
        with tempfile.TemporaryDirectory(prefix="bench-work-") as work, \
                tempfile.TemporaryDirectory(prefix="bench-out-") as out:
            workdir, outdir = Path(work), Path(out)
            (workdir / "implementation.py").write_text(code, encoding="utf-8")
            (workdir / "test_task.py").write_bytes(task.test_file.read_bytes())
            (workdir / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
            pristine = {n: _sha256(workdir / n) for n in _WORKDIR_FILES}

            try:
                outcome = self.sandbox.run(workdir, outdir, timeout)
            except Exception as e:  # sandbox could not start (e.g. docker missing)
                return result(TestStatus.ERROR, error_message=f"sandbox failure: {e}")

            if outcome.timed_out:
                return result(
                    TestStatus.TIMEOUT,
                    stdout=outcome.stdout,
                    stderr=outcome.stderr,
                    error_message=f"Execution exceeded {timeout}s (hang or infinite loop).",
                )

            tampered = self._tampering(workdir, pristine)
            if tampered:
                return result(
                    TestStatus.ERROR, stdout=outcome.stdout, stderr=outcome.stderr,
                    error_message=tampered,
                )

            return self._score(task, outcome, outdir / JUNIT_NAME, result)

    @staticmethod
    def _tampering(workdir: Path, pristine: Dict[str, str]) -> Optional[str]:
        present = {p.name for p in workdir.iterdir()}
        extra = present - _WORKDIR_FILES
        if extra:
            return f"workspace tampering: unexpected files created {sorted(extra)}"
        for name in ("test_task.py", "pytest.ini"):
            if not (workdir / name).exists() or _sha256(workdir / name) != pristine[name]:
                return f"workspace tampering: {name} was modified"
        return None

    @staticmethod
    def _score(task, outcome, report: Path, result) -> BenchmarkResult:
        base = {"stdout": outcome.stdout, "stderr": outcome.stderr}
        if not report.is_file():
            return result(
                TestStatus.ERROR,
                error_message=(
                    "No JUnit report produced (interpreter exited or crashed before pytest "
                    f"finished; exit code {outcome.returncode})."
                ),
                **base,
            )
        try:
            outcomes = parse_junit(report)
        except (ET.ParseError, ValueError, OSError) as e:
            return result(TestStatus.ERROR, error_message=f"Unreadable JUnit report: {e}", **base)

        expected = set(task.meta.expected_tests)
        reported = set(outcomes)
        if reported != expected:
            missing, extra = sorted(expected - reported), sorted(reported - expected)
            return result(
                TestStatus.ERROR,
                error_message=(
                    f"Test set mismatch (missing={missing}, unexpected={extra}); "
                    "collection failed or tests were altered."
                ),
                **base,
            )

        passed = sorted(t for t, o in outcomes.items() if o == "passed")
        failed = sorted(t for t, o in outcomes.items() if o != "passed")
        f2p, p2p = task.meta.fail_to_pass, task.meta.pass_to_pass
        resolved = not failed
        if resolved != (outcome.returncode == 0):
            return result(
                TestStatus.ERROR,
                error_message=(
                    f"Inconsistent run: report says resolved={resolved} "
                    f"but exit code was {outcome.returncode}."
                ),
                **base,
            )
        return result(
            TestStatus.RESOLVED if resolved else TestStatus.FAILED,
            resolved=resolved,
            passed_tests=len(passed),
            passed_test_ids=passed,
            failed_test_ids=failed,
            fail_to_pass_passed=sum(outcomes[t] == "passed" for t in f2p),
            pass_to_pass_passed=sum(outcomes[t] == "passed" for t in p2p),
            error_message=None if resolved else f"{len(failed)} expected test(s) did not pass: {failed}",
            **base,
        )
