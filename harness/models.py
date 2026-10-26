"""Pydantic schemas shared by the evaluator, the CLI and the task loader."""

import hashlib
import platform
import subprocess
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


@lru_cache(maxsize=1)
def get_git_sha() -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class TestStatus(str, Enum):
    __test__ = False  # not a pytest test class

    RESOLVED = "RESOLVED"  # every FAIL_TO_PASS and PASS_TO_PASS test passed
    FAILED = "FAILED"  # tests ran but at least one expected test did not pass
    ERROR = "ERROR"  # harness-level failure: crash, no/invalid report, tampering
    PATCH_ERROR = "PATCH_ERROR"  # patch task: the diff is malformed, forbidden or does not apply
    TIMEOUT = "TIMEOUT"
    ANTI_CHEAT_VIOLATION = "ANTI_CHEAT_VIOLATION"


class Edit(BaseModel):
    """A literal text replacement applied to the reference solution."""

    find: str
    replace: str
    count: int = 1  # exact number of occurrences of ``find`` expected in the solution
    file: Optional[str] = None  # patch tasks: file (relative to the repo) the edit applies to


class Mutant(BaseModel):
    """A plausible-but-wrong variant of the gold solution that the tests must reject."""

    name: str
    reason: str
    edits: List[Edit]


class TaskMeta(BaseModel):
    """Contents of ``task.json``."""

    title: str
    difficulty: str
    language: str = "Python"
    kind: Literal["single_file", "patch"] = "single_file"
    timeout_seconds: int = 60
    allowed_imports: List[str]
    editable_paths: List[str] = Field(default_factory=list)  # patch tasks: dirs/files a patch may touch
    fail_to_pass: List[str]
    pass_to_pass: List[str]
    mutants: List[Mutant] = Field(default_factory=list)

    @property
    def expected_tests(self) -> List[str]:
        return self.fail_to_pass + self.pass_to_pass


class TaskDefinition(BaseModel):
    task_id: str
    meta: TaskMeta
    path: Path
    problem_statement: str
    baseline_file: Optional[Path] = None  # baseline.py (single_file); patch tasks use the unmodified repo
    solution_file: Path  # solution.py (single_file) or solution.patch (patch)
    test_file: Path

    def gold_candidate(self) -> str:
        return self.solution_file.read_text(encoding="utf-8")

    def baseline_candidate(self) -> str:
        """What the 'before' state submits: the buggy file, or an empty patch."""
        return self.baseline_file.read_text(encoding="utf-8") if self.baseline_file else ""

    @property
    def title(self) -> str:
        return self.meta.title

    @property
    def difficulty(self) -> str:
        return self.meta.difficulty

    def content_hash(self) -> str:
        """Deterministic sha256 digest of problem statement, tests, metadata, and reference files."""
        hasher = hashlib.sha256()
        for p in sorted(self.path.rglob("*")):
            if p.is_file() and not (set(p.parts) & {"__pycache__", ".pytest_cache"}):
                rel = p.relative_to(self.path).as_posix()
                hasher.update(rel.encode("utf-8"))
                hasher.update(p.read_bytes())
        return hasher.hexdigest()


class Provenance(BaseModel):
    harness_git_sha: Optional[str] = None
    image_digest: Optional[str] = None
    task_content_hash: str
    python_version: str = Field(default_factory=platform.python_version)
    backend: str = "local"
    timeout_seconds: int = 60
    seed: Optional[int] = 0
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class BenchmarkResult(BaseModel):
    task_id: str
    status: TestStatus
    resolved: bool = False
    passed_tests: int = 0
    total_tests: int = 0
    passed_test_ids: List[str] = Field(default_factory=list)
    failed_test_ids: List[str] = Field(default_factory=list)
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    error_message: Optional[str] = None
    anti_cheat_violations: List[str] = Field(default_factory=list)
    provenance: Optional[Provenance] = None
