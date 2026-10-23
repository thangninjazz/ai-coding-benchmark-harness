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

import os
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

MAX_OUTPUT_CHARS = 20_000
JUNIT_NAME = "junit.xml"

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

    def run(self, workdir: Path, outdir: Path, timeout: int) -> SandboxOutcome: ...


def _tail(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else "...[truncated]...\n" + text[-MAX_OUTPUT_CHARS:]


def pytest_args(junit_path: str, timeout: int) -> List[str]:
    """Arguments after the interpreter. Plugin autoload is off (see env) so the run is
    independent of whatever third-party pytest plugins happen to be installed."""
    per_test = max(5, timeout // 2)
    return [
        "-m", "pytest", "test_task.py",
        "-c", "pytest.ini",
        "--rootdir=.",
        "-p", "no:cacheprovider",
        "-p", "pytest_asyncio.plugin",
        "-p", "pytest_timeout",
        f"--timeout={per_test}",
        f"--junitxml={junit_path}",
        "--tb=short",
        "-q",
    ]


def scrubbed_env(workdir: Path) -> Dict[str, str]:
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

    def run(self, workdir: Path, outdir: Path, timeout: int) -> SandboxOutcome:
        args = pytest_args(str(outdir / JUNIT_NAME), timeout)
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
            env=scrubbed_env(workdir),
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

    def build_command(self, workdir: Path, outdir: Path, timeout: int, container: str) -> List[str]:
        return [
            "docker", "run", "--rm", "--name", container,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1",
            "--user", "1001:1001",
            "--tmpfs", "/tmp:rw,size=64m",
            "--mount", f"type=bind,source={workdir},target=/submission,readonly",
            "--mount", f"type=bind,source={outdir},target=/out",
            "-w", "/submission",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONHASHSEED=0",
            "-e", "PYTHONUTF8=1",
            "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--entrypoint", "python",
            self.image,
            *pytest_args(f"/out/{JUNIT_NAME}", timeout),
        ]

    def run(self, workdir: Path, outdir: Path, timeout: int) -> SandboxOutcome:
        if sys.platform != "win32":
            os.chmod(outdir, 0o777)  # container user is not the host user
        container = f"bench-{uuid.uuid4().hex[:12]}"
        proc = subprocess.Popen(
            self.build_command(workdir, outdir, timeout, container),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def kill() -> None:
            subprocess.run(["docker", "kill", container], capture_output=True, check=False)
            proc.kill()

        return _communicate(proc, timeout, kill)


def make_sandbox(backend: str, image: str = "ai-benchmark-harness:latest") -> Sandbox:
    if backend == "local":
        return LocalSandbox()
    if backend == "docker":
        return DockerSandbox(image)
    raise ValueError(f"unknown backend: {backend!r} (expected 'local' or 'docker')")
