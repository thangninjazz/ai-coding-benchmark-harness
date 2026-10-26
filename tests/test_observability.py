import json
import logging

from harness.cli import main
from harness.models import BenchmarkResult, TestStatus
from harness.observability import (
    ExitCode,
    JsonLogFormatter,
    MetricsCollector,
    RedactingFilter,
    redact_secrets,
)

from .conftest import TASK1


def test_redact_secrets():
    token_hex = "a" * 64
    text = f"Token is {token_hex} and key is sk-ant-api03-abcdef123456"
    redacted = redact_secrets(text)
    assert token_hex not in redacted
    assert "sk-ant-api03" not in redacted
    assert "[REDACTED]" in redacted


def test_redacting_filter_and_json_formatter():
    logger = logging.getLogger("test_redact")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    class MockHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(self.format(record))

    handler = MockHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)

    sample_hash = "f" * 64
    logger.info("Processing item hash: %s", sample_hash)

    assert len(handler.lines) == 1
    log_data = json.loads(handler.lines[0])
    assert log_data["level"] == "INFO"
    assert sample_hash not in log_data["message"]
    assert "[REDACTED]" in log_data["message"]
    assert "timestamp" in log_data


def test_metrics_collector():
    collector = MetricsCollector()
    r1 = BenchmarkResult(task_id="t1", status=TestStatus.RESOLVED, resolved=True, duration_seconds=1.5)
    r2 = BenchmarkResult(task_id="t2", status=TestStatus.FAILED, resolved=False, duration_seconds=2.5)

    collector.record(r1)
    collector.record(r2)

    summary = collector.summary()
    assert summary["total_runs"] == 2
    assert summary["resolved_runs"] == 1
    assert summary["resolve_rate"] == 0.5
    assert summary["status_counts"]["RESOLVED"] == 1
    assert summary["status_counts"]["FAILED"] == 1
    assert summary["durations"]["min_seconds"] == 1.5
    assert summary["durations"]["max_seconds"] == 2.5
    assert summary["durations"]["total_seconds"] == 4.0


def test_cli_exit_codes_and_flags(capsys, monkeypatch):
    # Test USAGE_ERROR
    try:
        assert main(["run", "invalid_task_name"]) == ExitCode.USAGE_ERROR.value
    except SystemExit as e:
        assert e.code == ExitCode.USAGE_ERROR.value

    # Test baseline failure gives ExitCode.FAILED
    code = main(["run", TASK1])
    assert code == ExitCode.FAILED.value

    # Test metrics flag prints metrics summary
    main(["run", TASK1, "--metrics"])
    out = capsys.readouterr().out
    assert "Metrics Summary:" in out

    # Test timeout exit code
    from harness import cli
    mock_res_timeout = BenchmarkResult(
        task_id=TASK1, status=TestStatus.TIMEOUT, resolved=False, duration_seconds=5.0
    )
    monkeypatch.setattr(cli.TaskEvaluator, "evaluate_task", lambda *a, **kw: mock_res_timeout)
    assert main(["run", TASK1]) == ExitCode.TIMEOUT.value

    # Test anti-cheat violation exit code
    mock_res_cheat = BenchmarkResult(
        task_id=TASK1,
        status=TestStatus.ANTI_CHEAT_VIOLATION,
        resolved=False,
        duration_seconds=0.1,
        anti_cheat_violations=["AC001: forbidden os"],
    )
    monkeypatch.setattr(cli.TaskEvaluator, "evaluate_task", lambda *a, **kw: mock_res_cheat)
    assert main(["run", TASK1]) == ExitCode.TAMPERING_OR_VIOLATION.value
