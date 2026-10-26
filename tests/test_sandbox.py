import subprocess
import sys
from pathlib import Path

import pytest

from harness.sandbox import DockerSandbox, make_sandbox, pytest_args, scrubbed_env


def test_scrubbed_env_hides_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws_secret")
    env = scrubbed_env(tmp_path)
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["HOME"] == str(tmp_path)


def test_pytest_args_pin_plugins_explicitly():
    args = pytest_args("/out/junit.xml", 60)
    assert "pytest_asyncio.plugin" in args and "pytest_timeout" in args
    assert "--junitxml=/out/junit.xml" in args
    assert "no:cacheprovider" in args


def test_docker_command_is_locked_down():
    cmd = DockerSandbox("img:tag").build_command(Path("/w"), Path("/o"), 60, "name")
    joined = " ".join(cmd)
    for flag in (
        "--network none", "--read-only", "--cap-drop ALL", "no-new-privileges",
        "--pids-limit", "--memory", "--user 1001:1001",
        "--ulimit nofile=256:256", "--ulimit nproc=128:128", "--ulimit fsize=16777216:16777216",
        "--ulimit core=0:0",
    ):
        assert flag in joined, flag
    assert any(a.startswith("type=bind,source=") and a.endswith("readonly") for a in cmd)
    assert cmd[cmd.index("--entrypoint") + 1] == "python"
    assert "img:tag" in cmd


def test_docker_sandbox_active_containers_cleanup():
    from harness.sandbox import _ACTIVE_CONTAINERS, _cleanup_all_containers

    _ACTIVE_CONTAINERS.add("bench-dummy-test-container")
    assert "bench-dummy-test-container" in _ACTIVE_CONTAINERS
    _cleanup_all_containers()
    assert len(_ACTIVE_CONTAINERS) == 0


def test_make_sandbox():
    assert make_sandbox("local").name == "local"
    assert make_sandbox("docker").name == "docker"
    with pytest.raises(ValueError):
        make_sandbox("vm")


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")


@posix_only
def test_launcher_runs_the_target_and_enforces_the_file_size_limit(tmp_path):
    from harness.sandbox import _LAUNCHER

    ok = subprocess.run(
        [sys.executable, "-c", _LAUNCHER, "30", sys.executable, "-c", "print('hi')"],
        capture_output=True, text=True,
    )
    assert ok.returncode == 0 and ok.stdout.strip() == "hi"

    big = "open('big', 'wb').write(b'x' * (32 << 20))"
    hog = subprocess.run(
        [sys.executable, "-c", _LAUNCHER, "30", sys.executable, "-c", big],
        cwd=tmp_path, capture_output=True,
    )
    assert hog.returncode != 0
    assert (tmp_path / "big").stat().st_size <= 16 << 20
