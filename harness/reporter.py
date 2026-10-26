"""
Authenticated out-of-band test reporter for ai-coding-benchmark-harness.

Replaces file-based JUnit reports (--junitxml) with an authenticated event stream
over stdout/stderr and a dedicated event channel signed with an ephemeral cryptographic secret.

Eliminates:
- Report forgery via sys.argv / disk inspection;
- In-process monkeypatching of _pytest.reports.TestReport;
- Early os._exit(1) spoofing.
"""

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EVENT_PREFIX = "__HARNESS_EVENT__:"
_EVENT_LINE_RE = re.compile(r"^" + re.escape(EVENT_PREFIX) + r"(\{.*\})\s*$")

REPORTER_FILE = "_harness_reporter.py"

REPORTER_SOURCE = '''"""
Internal pytest reporter plugin. Streams HMAC-signed test events.
"""
import hashlib
import hmac
import json
import os
import sys
import pytest

_PREFIX = "__HARNESS_EVENT__:"
_SECRET = None
_EVENT_FILE = None


def pytest_load_initial_conftests(early_config, parser, args):
    global _SECRET, _EVENT_FILE
    # Extract secret and event file path from environment and scrub immediately
    _SECRET = os.environ.pop("_HARNESS_SECRET_TOKEN", None)
    _EVENT_FILE = os.environ.pop("_HARNESS_EVENT_FILE", None)
    # Also clean up any argv if passed
    for a in list(sys.argv):
        if a.startswith("--harness-token="):
            _SECRET = a.split("=", 1)[1]
            sys.argv.remove(a)
        elif a.startswith("--harness-event-file="):
            _EVENT_FILE = a.split("=", 1)[1]
            sys.argv.remove(a)


def _sign(canonical_json: str) -> str:
    if not _SECRET:
        return ""
    return hmac.new(_SECRET.encode("utf-8"), canonical_json.encode("utf-8"), hashlib.sha256).hexdigest()


def _emit(event_type: str, data: dict):
    payload = {"event": event_type, **data}
    canonical = json.dumps(payload, sort_keys=True)
    sig = _sign(canonical)
    msg = json.dumps({"payload": payload, "sig": sig})
    line = f"{_PREFIX}{msg}\\n"
    if _EVENT_FILE:
        try:
            with open(_EVENT_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
    # Also stream to stderr directly
    dest = getattr(sys, "__stderr__", sys.stderr)
    dest.write(f"\\n{line}")
    dest.flush()


def _test_id(item) -> str:
    mod = getattr(item, "module", None)
    mod_name = mod.__name__ if mod else ""
    cls = getattr(item, "cls", None)
    name = item.name
    if cls is not None:
        name = f"{cls.__name__}.{name}"
    if mod_name and mod_name != "test_task":
        return f"{mod_name}.{name}"
    return name


class _ReporterHook:
    def __init__(self):
        self.recorded = {}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        tid = _test_id(item)
        if call.excinfo is not None:
            # An actual exception was raised (genuine test or fixture failure)
            is_skip = getattr(call.excinfo, "typename", "") == "Skipped"
            status = "skipped" if is_skip else "failed"
            self.recorded[tid] = status
            _emit("test_outcome", {"test_id": tid, "outcome": status})
        elif call.when == "call" and tid not in self.recorded:
            # Genuine test passed with no exceptions
            self.recorded[tid] = "passed"
            _emit("test_outcome", {"test_id": tid, "outcome": "passed"})


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config.pluginmanager.register(_ReporterHook(), "_harness_reporter_hook")


def pytest_sessionfinish(session, exitstatus):
    _emit("session_finish", {"exitstatus": exitstatus})
'''


def sign_payload(token: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True)
    return hmac.new(token.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_stream(
    text: str, token: str, event_file: Optional[Path] = None
) -> Tuple[Dict[str, str], bool, List[str]]:
    """Parse authenticated events from runner output and/or event file.

    Returns:
        (outcomes, session_finished, warnings)
    """
    outcomes: Dict[str, str] = {}
    session_finished = False
    warnings: List[str] = []

    lines: List[str] = []
    if event_file and event_file.is_file():
        try:
            lines.extend(event_file.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            warnings.append(f"Failed to read event file: {e}")

    lines.extend(text.splitlines())

    for line in lines:
        line = line.strip()
        if not line.startswith(EVENT_PREFIX):
            continue
        raw_json = line[len(EVENT_PREFIX):].strip()
        try:
            wrapper = json.loads(raw_json)
            payload = wrapper.get("payload", {})
            sig = wrapper.get("sig", "")
            expected_sig = sign_payload(token, payload)
            if not hmac.compare_digest(sig, expected_sig):
                warnings.append(f"Rejected unauthenticated event signature: {payload}")
                continue

            event_type = payload.get("event")
            if event_type == "test_outcome":
                tid = payload.get("test_id")
                outcome = payload.get("outcome")
                if tid and outcome and outcomes.get(tid) != "failed":
                    outcomes[tid] = outcome
            elif event_type == "session_finish":
                session_finished = True
        except (json.JSONDecodeError, AttributeError) as e:
            warnings.append(f"Malformed event line: {e}")

    return outcomes, session_finished, warnings


def strip_events(text: str) -> str:
    """Remove __HARNESS_EVENT__: lines from output so logs remain readable."""
    lines = [line for line in text.splitlines() if not line.strip().startswith(EVENT_PREFIX)]
    return "\n".join(lines)
