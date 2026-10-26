"""Observability module: structured JSON logging, secret redaction, metrics collection, and exit codes."""

import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

from harness.models import BenchmarkResult

# Patterns for secrets to scrub from log streams
SECRET_PATTERNS = [
    re.compile(r"([0-9a-fA-F]{64})"),  # 256-bit hex tokens
    re.compile(r"(_HARNESS_SECRET_TOKEN=)[^\s]+"),
    re.compile(r"(sk-ant-[a-zA-Z0-9_\-]+)"),  # Anthropic API keys
    re.compile(r"(ANTHROPIC_API_KEY=)[^\s]+"),
]


class ExitCode(int, Enum):
    RESOLVED = 0
    FAILED = 1
    USAGE_ERROR = 2
    TIMEOUT = 3
    TAMPERING_OR_VIOLATION = 4


def redact_secrets(text: str) -> str:
    """Mask any cryptographic tokens, environment secrets, or provider API keys."""
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pat in SECRET_PATTERNS:
        scrubbed = pat.sub("[REDACTED]", scrubbed)
    return scrubbed


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive patterns from all record arguments and messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        return True


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        msg = redact_secrets(record.getMessage())
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Include extra fields attached to the log record
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "message",
            } and not key.startswith("_"):
                payload[key] = val
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(json_output: bool = False, level: str = "INFO") -> logging.Logger:
    """Configure harness root logger with redaction and optional JSON output."""
    logger = logging.getLogger("harness")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())

    if json_output:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(handler)
    return logger


class MetricsCollector:
    """Collects outcome counts, durations, and summary metrics for benchmark runs."""

    def __init__(self) -> None:
        self.counts: Dict[str, int] = {}
        self.durations: List[float] = []
        self._results: List[BenchmarkResult] = []

    def record(self, result: BenchmarkResult) -> None:
        status_key = result.status.value
        self.counts[status_key] = self.counts.get(status_key, 0) + 1
        self.durations.append(result.duration_seconds)
        self._results.append(result)

    def summary(self) -> Dict[str, Any]:
        total = sum(self.counts.values())
        resolved = self.counts.get("RESOLVED", 0)
        return {
            "total_runs": total,
            "resolved_runs": resolved,
            "resolve_rate": (resolved / total) if total > 0 else 0.0,
            "status_counts": dict(self.counts),
            "durations": {
                "min_seconds": round(min(self.durations), 4) if self.durations else 0.0,
                "mean_seconds": (
                    round(sum(self.durations) / len(self.durations), 4)
                    if self.durations
                    else 0.0
                ),
                "max_seconds": round(max(self.durations), 4) if self.durations else 0.0,
                "total_seconds": round(sum(self.durations), 4),
            },
        }
