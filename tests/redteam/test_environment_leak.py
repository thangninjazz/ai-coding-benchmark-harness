"""Red-team tests: environment and secret leak prevention, file descriptor isolation.

Submissions must never be able to inspect parent secrets, tokens, or write to inherited fds.
Network access must be blocked.
"""




def test_parent_secrets_not_leaked(local_evaluator, target_tasks, no_static_scan, monkeypatch):
    task = target_tasks["single"]
    monkeypatch.setenv("SUPERVISOR_SECRET_KEY", "super_secret_token_12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-token-abcdef")
    
    cheat = """
import os
leaked = []
for k in ("SUPERVISOR_SECRET_KEY", "ANTHROPIC_API_KEY"):
    if k in os.environ:
        leaked.append(k)

if leaked:
    raise RuntimeError(f"Leaked environment variables: {leaked}")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
    # Ensure it failed because baseline is not implemented, NOT because it raised RuntimeError
    assert "Leaked environment variables" not in (res.stdout + res.stderr)


def test_network_access_blocked_in_container(docker_evaluator_opt, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import socket

try:
    s = socket.create_connection(("8.8.8.8", 53), timeout=2.0)
    s.close()
    connected = True
except Exception:
    connected = False

if connected:
    raise RuntimeError("Outbound network connection succeeded in sandbox!")

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = docker_evaluator_opt.evaluate_code(task, cheat)
    assert not res.resolved
    assert "Outbound network connection succeeded" not in (res.stdout + res.stderr)


def test_inherited_file_descriptors_not_writable(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    cheat = """
import os

# Fds 0 (stdin), 1 (stdout), 2 (stderr) are standard.
# Check higher fds (3..20). In a secure sandbox, candidate shouldn't have access to supervisor fds.
for fd in range(3, 20):
    try:
        os.write(fd, b"exploit")
    except OSError:
        pass

class ConcurrentLRUCache:
    def __init__(self, capacity: int = 1): pass
"""
    res = local_evaluator.evaluate_code(task, cheat)
    assert not res.resolved
