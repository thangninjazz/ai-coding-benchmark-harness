import json
import shutil
from pathlib import Path

from harness.evaluator import TaskEvaluator
from harness.models import BenchmarkResult
from harness.sandbox import make_sandbox

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
SCHEMA_PATH = ROOT / "harness" / "schema" / "result.schema.json"


def test_schema_file_exists_and_matches_model():
    assert SCHEMA_PATH.is_file(), f"Missing schema at {SCHEMA_PATH}"
    schema_on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    model_schema = BenchmarkResult.model_json_schema()
    assert schema_on_disk == model_schema


def test_provenance_recorded_in_evaluation(tmp_path):
    evaluator = TaskEvaluator(TASKS_DIR, sandbox=make_sandbox("local"), seed=42)
    tasks = evaluator.discover_tasks()
    assert tasks, "No tasks discovered"
    task = tasks[0]
    result = evaluator.evaluate_task(task, use_gold=True)

    assert result.provenance is not None
    prov = result.provenance
    assert prov.backend == "local"
    assert prov.seed == 42
    assert prov.task_content_hash == task.content_hash()
    assert prov.python_version is not None
    assert prov.timestamp_utc is not None

    # Test serialization to JSON matches BenchmarkResult schema validation
    dumped = json.loads(result.model_dump_json())
    validated = BenchmarkResult.model_validate(dumped)
    assert validated.task_id == result.task_id
    assert validated.status == result.status
    assert validated.provenance is not None
    assert validated.provenance.seed == 42


def test_task_content_hash_deterministic_and_sensitive(tmp_path):
    evaluator = TaskEvaluator(TASKS_DIR)
    task = evaluator.discover_tasks()[0]

    # Copy task to a temp directory
    dest = tmp_path / task.task_id
    shutil.copytree(task.path, dest)

    evaluator_temp = TaskEvaluator(tmp_path)
    task1 = evaluator_temp.load_task(dest)
    hash1 = task1.content_hash()
    assert isinstance(hash1, str) and len(hash1) == 64

    # Recalculating without changes gives identical hash
    assert task1.content_hash() == hash1

    # Modifying problem.md changes hash
    (dest / "problem.md").write_text("Modified problem statement\n", encoding="utf-8")
    task2 = evaluator_temp.load_task(dest)
    assert task2.content_hash() != hash1


def test_evaluation_reproducibility():
    evaluator = TaskEvaluator(TASKS_DIR, sandbox=make_sandbox("local"), seed=123)
    task = evaluator.discover_tasks()[0]

    res1 = evaluator.evaluate_task(task, use_gold=True)
    res2 = evaluator.evaluate_task(task, use_gold=True)

    d1 = res1.model_dump()
    d2 = res2.model_dump()

    # Ignore duration_seconds and timestamp_utc
    d1.pop("duration_seconds")
    d2.pop("duration_seconds")
    d1["provenance"].pop("timestamp_utc")
    d2["provenance"].pop("timestamp_utc")

    # Pytest summary line prints elapsed wall clock time (e.g., "in 6.23s"), normalize it
    import re
    d1["stdout"] = re.sub(r" in \d+\.\d+s", " in <TIMING>s", d1["stdout"])
    d2["stdout"] = re.sub(r" in \d+\.\d+s", " in <TIMING>s", d2["stdout"])

    assert d1 == d2
