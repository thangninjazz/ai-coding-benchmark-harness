"""
Execution backends.

A backend receives a *work directory* (``implementation.py``, ``test_task.py``,
``pytest.ini``) and an *output directory*, runs pytest against them and leaves a JUnit
XML report in the output directory. The evaluator never trusts the process exit code
alone; it validates the report (see ``evaluator.py``).

* ``LocalSandbox``  - subprocess with a scrubbed environment, POSIX resource limits and
  process-group kill on timeout. Convenient for development; shares the host kernel,
  filesystem and network, so it is **not** a security boundary.
* ``DockerSandbox`` - ``docker run --network none --read-only --cap-drop ALL`` with
  memory/PID/CPU limits and a read-only submission mount. This is the backend to use for
  untrusted code.
"""

import atexit
import os
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set

MAX_OUTPUT_CHARS = 20_000
JUNIT_NAME = "junit.xml"

_ACTIVE_CONTAINERS: Set[str] = set()


def _cleanup_all_containers() -> None:
    for cid in list(_ACTIVE_CONTAINERS):
        try:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)
        except OSError:
            pass
    _ACTIVE_CONTAINERS.clear()


atexit.register(_cleanup_all_containers)

# A test the harness adds to every run. It always fails. If the report ever says it passed,
# the reporting path was tampered with (e.g. pytest monkeypatched by the candidate).
CANARY_FILE = "_harness_canary.py"
CANARY_ID = "_harness_canary.test_canary_must_fail"
CANARY_SOURCE = """def test_canary_must_fail():
    assert False, "intentional: proves that failures are reported"
"""

PYTEST_INI = """[pytest]
asyncio_mode = strict
asyncio_default_fixture_loop_scope = function
pythonpath = .
"""

# Environment variables forwarded to the local sandbox; everything else is dropped so
# that tokens and credentials in the parent environment are invisible to candidate code.
_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")


@dataclass
class SandboxOutcome:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool


class Sandbox(Protocol):
    name: str

    def run(
        self, workdir: Path, outdir: Path, timeout: int, session_token: Optional[str] = None
    ) -> SandboxOutcome: ...


def _tail(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else "...[truncated]...\n" + text[-MAX_OUTPUT_CHARS:]


def pytest_args(
    junit_or_timeout: Any = None,
    timeout_or_junit: Any = None,
    *,
    timeout: Optional[int] = None,
    junit_path: Optional[str] = None,
) -> List[str]:
    """Arguments after the interpreter. Plugin autoload is off (see env) so the run is
    independent of whatever third-party pytest plugins happen to be installed."""
    resolved_timeout = 60
    resolved_junit = None
    if isinstance(junit_or_timeout, int | float):
        resolved_timeout = int(junit_or_timeout)
        if isinstance(timeout_or_junit, str):
            resolved_junit = timeout_or_junit
    elif isinstance(junit_or_timeout, str):
        resolved_junit = junit_or_timeout
        if isinstance(timeout_or_junit, int | float):
            resolved_timeout = int(timeout_or_junit)
    if timeout is not None:
        resolved_timeout = timeout
    if junit_path is not None:
        resolved_junit = junit_path

    per_test = max(5, resolved_timeout // 2)
    args = [
        "-m", "pytest", "test_task.py", CANARY_FILE,
        "-c", "pytest.ini",
        "--rootdir=.",
        "-p", "no:cacheprovider",
        "-p", "pytest_asyncio.plugin",
        "-p", "pytest_timeout",
        "-p", "_harness_reporter",
        f"--timeout={per_test}",
        "--tb=short",
        "-q",
    ]
    if resolved_junit:
        args.append(f"--junitxml={resolved_junit}")
    return args


def scrubbed_env(
    workdir: Path, session_token: Optional[str] = None, event_file: Optional[Path] = None
) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    scratch = str(workdir)
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        HOME=scratch,
        TMPDIR=scratch,
        TEMP=scratch,
        TMP=scratch,
    )
    if session_token:
        env["_HARNESS_SECRET_TOKEN"] = session_token
    if event_file:
        env["_HARNESS_EVENT_FILE"] = str(event_file)
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()


# POSIX only. Applies resource limits and then replaces itself with pytest. This avoids
# ``preexec_fn``, which the subprocess docs flag as unsafe when threads are running.
_LAUNCHER = """
import os, resource, sys
cpu = int(sys.argv[1])
for res, value in (
    (resource.RLIMIT_CPU, cpu),
    (resource.RLIMIT_AS, 4 << 30),
    (resource.RLIMIT_FSIZE, 16 << 20),
    (resource.RLIMIT_NOFILE, 256),
    (resource.RLIMIT_CORE, 0),
):
    try:
        resource.setrlimit(res, (value, value))
    except (ValueError, OSError):
        pass
os.execv(sys.argv[2], sys.argv[2:])
"""


def _communicate(proc: subprocess.Popen, timeout: int, on_timeout) -> SandboxOutcome:
    try:
        out, err = proc.communicate(timeout=timeout)
        return SandboxOutcome(proc.returncode, _tail(out), _tail(err), False)
    except subprocess.TimeoutExpired:
        on_timeout()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return SandboxOutcome(None, _tail(out or ""), _tail(err or ""), True)


class LocalSandbox:
    name = "local"

    def run(
        self, workdir: Path, outdir: Path, timeout: int, session_token: Optional[str] = None
    ) -> SandboxOutcome:
        args = pytest_args(timeout=timeout)
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            cmd = [sys.executable, *args]
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            cmd = [sys.executable, "-c", _LAUNCHER, str(timeout + 5), sys.executable, *args]
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=scrubbed_env(
                workdir, session_token=session_token, event_file=outdir / "events.jsonl"
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        return _communicate(proc, timeout, lambda: _kill_tree(proc))


class DockerSandbox:
    name = "docker"

    def __init__(self, image: str = "ai-benchmark-harness:latest"):
        self.image = image

    def build_command(
        self,
        workdir: Path,
        outdir: Path,
        timeout: int,
        container: str,
        session_token: Optional[str] = None,
    ) -> List[str]:
        cmd = [
            "docker", "run", "--rm", "--name", container,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1",
            "--user", "1001:1001",
            "--ulimit", "nofile=256:256",
            "--ulimit", "nproc=128:128",
            "--ulimit", "fsize=16777216:16777216",
            "--ulimit", "core=0:0",
            "--tmpfs", "/tmp:rw,size=64m",
            "--mount", f"type=bind,source={workdir},target=/submission,readonly",
            "--mount", f"type=bind,source={outdir},target=/out",
            "-w", "/submission",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONHASHSEED=0",
            "-e", "PYTHONUTF8=1",
            "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "-e", "_HARNESS_EVENT_FILE=/out/events.jsonl",
        ]
        if session_token:
            cmd.extend(["-e", f"_HARNESS_SECRET_TOKEN={session_token}"])
        cmd.extend([
            "--entrypoint", "python",
            self.image,
            *pytest_args(timeout=timeout),
        ])
        return cmd

    def run(
        self, workdir: Path, outdir: Path, timeout: int, session_token: Optional[str] = None
    ) -> SandboxOutcome:
        if sys.platform != "win32":
            os.chmod(outdir, 0o777)  # container user is not the host user
        container = f"bench-{uuid.uuid4().hex[:12]}"
        _ACTIVE_CONTAINERS.add(container)
        try:
            proc = subprocess.Popen(
                self.build_command(
                    workdir, outdir, timeout, container, session_token=session_token
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            def kill() -> None:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
                proc.kill()

            return _communicate(proc, timeout, kill)
        finally:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container], capture_output=True, check=False
                )
            except OSError:
                pass
            _ACTIVE_CONTAINERS.discard(container)


def make_sandbox(backend: str, image: str = "ai-benchmark-harness:latest") -> Sandbox:
    if backend == "local":
        return LocalSandbox()
    if backend == "docker":
        return DockerSandbox(image)
    raise ValueError(f"unknown backend: {backend!r} (expected 'local' or 'docker')")
