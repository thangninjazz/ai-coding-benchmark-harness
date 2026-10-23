"""
Benchmark self-verification ("test the tests").

A task is only worth shipping if its test suite can tell good code from bad. For each
task this module checks, over ``repeat`` independent runs:

1. gold and baseline pass the static anti-cheat scan (no false positives);
2. the **gold** solution resolves the task every time (no flakiness);
3. the **baseline** is unresolved every time, every FAIL_TO_PASS test fails on it and
   every PASS_TO_PASS test passes on it (SWE-bench validity conditions);
4. every **mutant** - a plausible wrong variant of gold - is killed by the tests every
   time, i.e. the mutation score is 100%.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from harness.anti_cheat import scan_code
from harness.evaluator import TaskEvaluator
from harness.models import BenchmarkResult, Mutant, TaskDefinition, TestStatus


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerificationReport:
    task_id: str
    checks: List[Check] = field(default_factory=list)
    mutants_total: int = 0
    mutants_killed: int = 0

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def mutation_score(self) -> float:
        return self.mutants_killed / self.mutants_total if self.mutants_total else 1.0


def apply_mutant(solution: str, mutant: Mutant) -> str:
    """Apply a mutant's edits; each ``find`` must occur exactly ``count`` times."""
    code = solution
    for edit in mutant.edits:
        found = code.count(edit.find)
        if found != edit.count:
            raise ValueError(
                f"mutant {mutant.name!r}: expected {edit.count} occurrence(s) of "
                f"{edit.find!r}, found {found}"
            )
        code = code.replace(edit.find, edit.replace)
    if code == solution:
        raise ValueError(f"mutant {mutant.name!r} does not change the solution")
    return code


def _repeat(fn: Callable[[], BenchmarkResult], n: int) -> List[BenchmarkResult]:
    return [fn() for _ in range(n)]


def verify_task(
    evaluator: TaskEvaluator,
    task: TaskDefinition,
    repeat: int = 1,
    workers: int = 4,
    progress: Optional[Callable[[str], None]] = None,
) -> VerificationReport:
    report = VerificationReport(task.task_id)
    note = progress or (lambda _msg: None)
    gold = task.solution_file.read_text(encoding="utf-8")
    baseline = task.baseline_file.read_text(encoding="utf-8")
    meta = task.meta

    # 1. anti-cheat must not reject the reference material
    for label, code in (("gold", gold), ("baseline", baseline)):
        found = [str(v) for v in scan_code(code, meta.allowed_imports)]
        report.checks.append(
            Check(f"anti-cheat accepts {label}", not found, "; ".join(found))
        )

    # 2. gold resolves, deterministically
    note(f"{task.task_id}: gold x{repeat}")
    gold_runs = _repeat(lambda: evaluator.evaluate_task(task, use_gold=True), repeat)
    bad = [r for r in gold_runs if not r.resolved]
    report.checks.append(
        Check(
            f"gold resolves in {repeat}/{repeat} runs",
            not bad,
            f"{bad[0].status.value}: {bad[0].error_message}" if bad else "",
        )
    )

    # 3. baseline is a valid 'before' state
    note(f"{task.task_id}: baseline x{repeat}")
    base_runs = _repeat(lambda: evaluator.evaluate_task(task), repeat)
    status_ok = all(r.status == TestStatus.FAILED for r in base_runs)
    report.checks.append(
        Check(
            "baseline is unresolved (tests ran and failed)",
            status_ok,
            ", ".join(sorted({r.status.value for r in base_runs})),
        )
    )
    if status_ok:
        never_failed = [
            t for t in meta.fail_to_pass
            if any(t not in r.failed_test_ids for r in base_runs)
        ]
        report.checks.append(
            Check(
                "every FAIL_TO_PASS test fails on baseline in every run",
                not never_failed,
                f"passed on baseline at least once: {never_failed}" if never_failed else "",
            )
        )
        broken = sorted({t for r in base_runs for t in r.failed_test_ids if t in meta.pass_to_pass})
        report.checks.append(
            Check(
                "every PASS_TO_PASS test passes on baseline",
                not broken,
                f"failing on baseline: {broken}" if broken else "",
            )
        )

    # 4. mutants must all be killed, every time
    mutants: List[Tuple[Mutant, str]] = []
    for m in meta.mutants:
        try:
            mutants.append((m, apply_mutant(gold, m)))
        except ValueError as e:
            report.checks.append(Check(f"mutant is well-formed: {m.name}", False, str(e)))
    report.mutants_total = len(meta.mutants)

    def judge(item: Tuple[Mutant, str]) -> Check:
        mutant, code = item
        runs = [evaluator.evaluate_code(task, code) for _ in range(repeat)]
        survived = [r for r in runs if r.status not in (TestStatus.FAILED, TestStatus.TIMEOUT)]
        killers = sorted({t for r in runs for t in r.failed_test_ids})
        detail = (
            f"survived as {survived[0].status.value}: {survived[0].error_message}"
            if survived
            else "killed by " + (", ".join(killers) if killers else "timeout")
        )
        return Check(f"mutant killed: {mutant.name}", not survived, detail)

    note(f"{task.task_id}: {len(mutants)} mutants x{repeat}")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for check in pool.map(judge, mutants):
            report.checks.append(check)
            report.mutants_killed += check.ok
    return report


def collect_test_ids(task: TaskDefinition) -> List[str]:
    """Collect test ids from the task's test file (run against the gold solution)."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from harness.sandbox import PYTEST_INI, scrubbed_env

    with tempfile.TemporaryDirectory(prefix="bench-collect-") as d:
        work = Path(d)
        (work / "implementation.py").write_text(
            task.solution_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (work / "test_task.py").write_bytes(task.test_file.read_bytes())
        (work / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_task.py", "-c", "pytest.ini",
             "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin"],
            cwd=work, env=scrubbed_env(work), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"test collection failed:\n{proc.stdout}\n{proc.stderr}")
    prefix = "test_task.py::"
    return [line[len(prefix):] for line in proc.stdout.splitlines() if line.startswith(prefix)]


@dataclass
class Calibration:
    fail_to_pass: List[str]
    pass_to_pass: List[str]
    flaky: List[str]


def calibrate(evaluator: TaskEvaluator, task: TaskDefinition, repeat: int = 3) -> Calibration:
    """Derive FAIL_TO_PASS / PASS_TO_PASS by running the baseline ``repeat`` times."""
    ids = collect_test_ids(task)
    probe_meta = task.meta.model_copy(update={"fail_to_pass": [], "pass_to_pass": ids})
    probe = task.model_copy(update={"meta": probe_meta})

    gold = evaluator.evaluate_task(probe, use_gold=True)
    if not gold.resolved:
        raise RuntimeError(f"gold does not pass all collected tests: {gold.error_message}")

    runs = [evaluator.evaluate_task(probe) for _ in range(repeat)]
    errors = [r for r in runs if r.status not in (TestStatus.FAILED, TestStatus.RESOLVED)]
    if errors:
        raise RuntimeError(f"baseline run did not complete: {errors[0].status} {errors[0].error_message}")
    f2p = [t for t in ids if all(t in r.failed_test_ids for r in runs)]
    p2p = [t for t in ids if all(t not in r.failed_test_ids for r in runs)]
    flaky = [t for t in ids if t not in f2p and t not in p2p]
    return Calibration(f2p, p2p, flaky)
