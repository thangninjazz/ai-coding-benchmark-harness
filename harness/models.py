from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class TestStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    ANTI_CHEAT_VIOLATION = "ANTI_CHEAT_VIOLATION"


class TaskDefinition(BaseModel):
    task_id: str
    title: str
    difficulty: str
    language: str
    path: Path
    problem_statement: str
    baseline_file: Path
    test_file: Path
    gold_patch: Optional[Path] = None


class BenchmarkResult(BaseModel):
    task_id: str
    status: TestStatus
    passed_tests: int = 0
    total_tests: int = 0
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    error_message: Optional[str] = None
    anti_cheat_violations: List[str] = Field(default_factory=list)
