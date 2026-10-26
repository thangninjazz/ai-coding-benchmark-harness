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
import platform
import re
import secrets
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import ValidationError

from harness.models import (
    BenchmarkResult,
    Provenance,
    TaskDefinition,
    TaskMeta,
    TestStatus,
    get_git_sha,
)
from harness.reporter import REPORTER_FILE, REPORTER_SOURCE, parse_stream, strip_events
from harness.sandbox import (
    CANARY_FILE,
    CANARY_ID,
    CANARY_SOURCE,
    JUNIT_NAME,
    PYTEST_INI,
    Sandbox,
    make_sandbox,
)
from harness.workspace import stage_workspace

MAX_REPORT_BYTES = 5 * 1024 * 1024
_IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
# pytest-timeout's banner. On Windows its only method kills the whole pytest process, so a test
# that overruns leaves no JUnit report behind; the banner is how that case is recognised.
_PER_TEST_TIMEOUT = re.compile(r"\+{5,} Timeout \+{5,}")


class TaskLoadError(Exception):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> Dict[str, str]:
    """sha256 of every file under ``root`` (bytecode/cache directories excluded)."""
    return {
        p.relative_to(root).as_posix(): _sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not (set(p.relative_to(root).parts) & _IGNORED_DIRS)
    }


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
        seed: Optional[int] = 0,
    ):
        self.tasks_dir = Path(tasks_dir)
        self.timeout_override = timeout_seconds
        self.sandbox = sandbox or make_sandbox("local")
        self.seed = seed

    # ------------------------------------------------------------------ discovery
    def load_task(self, directory: Path) -> TaskDefinition:
        if not (directory / "task.json").is_file():
            raise TaskLoadError(f"{directory.name}: missing files ['task.json']")
        try:
            meta = TaskMeta.model_validate_json((directory / "task.json").read_text(encoding="utf-8"))
        except ValidationError as e:
            raise TaskLoadError(f"{directory.name}: invalid task.json\n{e}") from e

        if meta.kind == "patch":
            required = ["problem.md", "solution.patch", "test_task.py", "repo"]
            if not meta.editable_paths:
                raise TaskLoadError(f"{directory.name}: patch tasks need editable_paths")
        else:
            required = ["problem.md", "baseline.py", "solution.py", "test_task.py"]
        missing = [f for f in required if not (directory / f).exists()]
        if missing:
            raise TaskLoadError(f"{directory.name}: missing files {missing}")
        gold = "solution.patch" if meta.kind == "patch" else "solution.py"
        return TaskDefinition(
            task_id=directory.name,
            meta=meta,
            path=directory,
            problem_statement=(directory / "problem.md").read_text(encoding="utf-8"),
            baseline_file=None if meta.kind == "patch" else directory / "baseline.py",
            solution_file=directory / gold,
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
        """Evaluate gold (``use_gold``), a candidate, or - by default - the buggy baseline.

        ``candidate_code`` is the source of ``implementation.py`` for single-file tasks and a
        unified diff for patch tasks."""
        if use_gold:
            code = task.gold_candidate()
        elif candidate_code is not None:
            code = candidate_code
        else:
            code = task.baseline_candidate()
        return self.evaluate_code(task, code)

    def evaluate_code(self, task: TaskDefinition, code: str) -> BenchmarkResult:
        start = time.perf_counter()
        timeout = self.timeout_override or task.meta.timeout_seconds
        session_token = secrets.token_hex(32)
        backend_name = getattr(self.sandbox, "name", "local")
        image_digest = (
            getattr(self.sandbox, "image", None) if backend_name == "docker" else None
        )
        provenance = Provenance(
            harness_git_sha=get_git_sha(),
            image_digest=image_digest,
            task_content_hash=task.content_hash(),
            python_version=platform.python_version(),
            backend=backend_name,
            timeout_seconds=timeout,
            seed=self.seed,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

        def result(status: TestStatus, **kw) -> BenchmarkResult:
            return BenchmarkResult(
                task_id=task.task_id,
                status=status,
                duration_seconds=time.perf_counter() - start,
                fail_to_pass_total=len(task.meta.fail_to_pass),
                pass_to_pass_total=len(task.meta.pass_to_pass),
                total_tests=len(task.meta.expected_tests),
                provenance=provenance,
                **kw,
            )

        with (
            tempfile.TemporaryDirectory(prefix="bench-work-") as work,
            tempfile.TemporaryDirectory(prefix="bench-out-") as out,
        ):
            workdir, outdir = Path(work), Path(out)
            staged = stage_workspace(task, code, workdir)
            if staged.error:
                return result(TestStatus.PATCH_ERROR, error_message=staged.error)
            if staged.violations:
                return result(
                    TestStatus.ANTI_CHEAT_VIOLATION,
                    anti_cheat_violations=staged.violations,
                    error_message="Submission rejected by static anti-cheat checks.",
                )
            (workdir / "test_task.py").write_bytes(task.test_file.read_bytes())
            (workdir / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
            (workdir / CANARY_FILE).write_text(CANARY_SOURCE, encoding="utf-8")
            (workdir / REPORTER_FILE).write_text(REPORTER_SOURCE, encoding="utf-8")
            pristine = _hash_tree(workdir)

            try:
                outcome = self.sandbox.run(
                    workdir, outdir, timeout, session_token=session_token
                )
            except Exception as e:  # sandbox could not start (e.g. docker missing)
                return result(TestStatus.ERROR, error_message=f"sandbox failure: {e}")

            if outcome.timed_out:
                return result(
                    TestStatus.TIMEOUT,
                    stdout=strip_events(outcome.stdout),
                    stderr=strip_events(outcome.stderr),
                    error_message=f"Execution exceeded {timeout}s (hang or infinite loop).",
                )

            tampered = self._tampering(workdir, pristine)
            if tampered:
                return result(
                    TestStatus.ERROR,
                    stdout=strip_events(outcome.stdout),
                    stderr=strip_events(outcome.stderr),
                    error_message=tampered,
                )

            return self._score(
                task,
                outcome,
                outdir / JUNIT_NAME,
                result,
                session_token=session_token,
                event_file=outdir / "events.jsonl",
            )

    @staticmethod
    def _tampering(workdir: Path, pristine: Dict[str, str]) -> Optional[str]:
        now = _hash_tree(workdir)
        created = sorted(set(now) - set(pristine))
        if created:
            return f"workspace tampering: unexpected files created {created}"
        removed = sorted(set(pristine) - set(now))
        if removed:
            return f"workspace tampering: files removed {removed}"
        changed = sorted(f for f in now if now[f] != pristine[f])
        if changed:
            return f"workspace tampering: {changed} modified while the tests ran"
        return None

    @staticmethod
    def _score(
        task,
        outcome,
        report: Path,
        result,
        session_token: Optional[str] = None,
        event_file: Optional[Path] = None,
    ) -> BenchmarkResult:
        clean_stdout = strip_events(outcome.stdout)
        clean_stderr = strip_events(outcome.stderr)
        base = {"stdout": clean_stdout, "stderr": clean_stderr}
        if not report.is_file() and _PER_TEST_TIMEOUT.search(outcome.stdout + outcome.stderr):
            return result(
                TestStatus.TIMEOUT,
                error_message="A test exceeded the per-test time limit (hang or runaway computation).",
                **base,
            )

        if session_token:
            outcomes, finished, _ = parse_stream(
                outcome.stdout + "\n" + outcome.stderr, session_token, event_file=event_file
            )
            if not finished:
                return result(
                    TestStatus.ERROR,
                    error_message=(
                        "No test report completed / No JUnit report (interpreter exited, crashed, "
                        f"or was terminated before pytest finished; exit code {outcome.returncode})."
                    ),
                    **base,
                )
        elif report.is_file():
            try:
                outcomes = parse_junit(report)
            except (ET.ParseError, ValueError, OSError) as e:
                return result(TestStatus.ERROR, error_message=f"Unreadable JUnit report: {e}", **base)
        else:
            return result(
                TestStatus.ERROR,
                error_message=(
                    "No JUnit report produced (interpreter exited or crashed before pytest "
                    f"finished; exit code {outcome.returncode})."
                ),
                **base,
            )

        canary = outcomes.pop(CANARY_ID, None)
        if canary != "failed":
            return result(
                TestStatus.ERROR,
                error_message=(
                    "Reporting integrity check failed: the harness canary test, which always "
                    f"fails, was reported as {canary or 'missing'}. pytest's reporting was "
                    "tampered with or the run was cut short."
                ),
                **base,
            )
        if outcome.returncode != 1:
            return result(
                TestStatus.ERROR,
                error_message=(
                    f"Unexpected pytest exit code {outcome.returncode} (the always-failing "
                    "canary means a healthy run exits with 1)."
                ),
                **base,
            )

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
