"""Pydantic schemas shared by the evaluator, the CLI and the task loader."""

from enum import Enum
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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
