"""Pydantic schemas shared by the evaluator, the CLI and the task loader."""

from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


class TestStatus(str, Enum):
    __test__ = False  # not a pytest test class

    RESOLVED = "RESOLVED"  # every FAIL_TO_PASS and PASS_TO_PASS test passed
    FAILED = "FAILED"  # tests ran but at least one expected test did not pass
    ERROR = "ERROR"  # harness-level failure: crash, no/invalid report, tampering
    TIMEOUT = "TIMEOUT"
    ANTI_CHEAT_VIOLATION = "ANTI_CHEAT_VIOLATION"


class Edit(BaseModel):
    """A literal text replacement applied to the reference solution."""

    find: str
    replace: str
    count: int = 1  # exact number of occurrences of ``find`` expected in the solution


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
    timeout_seconds: int = 60
    allowed_imports: List[str]
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
    baseline_file: Path
    solution_file: Path
    test_file: Path

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
