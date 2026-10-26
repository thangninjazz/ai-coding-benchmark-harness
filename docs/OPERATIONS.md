# Production Operations Guide

This guide details runner sizing, container security configurations, observability, exit codes, and task upgrade procedures for deploying `ai-coding-benchmark-harness` in production evaluation pipelines.

---

## 1. Runner Sizing and Concurrency

When running adversarial or unknown language model submissions, evaluations must run with strict resource bounds to avoid cascading noisy-neighbor interference or resource starvation.

### Resource Recommendations per Runner

| Resource | Recommended Allocation per Task Worker |
|---|---|
| **CPU** | 1 to 2 vCPUs |
| **RAM** | 1 GB memory limit (`--memory 1g`) |
| **PIDs / Threads** | 256 (`--pids-limit 256`) |
| **Tmpfs** | 128 MB (`--tmpfs /tmp:rw,size=128m`) |
| **Open Files** | 256 (`--ulimit nofile=256:256`) |
| **Max Process Limits** | 128 (`--ulimit nproc=128:128`) |
| **Max File Output** | 16 MB (`--ulimit fsize=16777216:16777216`) |

### Parallelism & Worker Pools

For high-throughput evaluation of model samples (e.g. `eval-model` or `verify`):
- Set `--workers` equal to `min(allocated_cpus // 2, 8)`.
- Over-subscribing CPUs will introduce artificial jitter into timing-sensitive concurrency and rate limiter tasks (e.g., `task_001`, `task_002`).

---

## 2. Container Isolation Hardening

The harness supports two sandboxing backends:
1. `LocalSandbox`: For rapid local development and unit tests.
2. `DockerSandbox`: Required for all adversarial or untrusted candidate code.

### Standard Production Docker Invocations

All Docker containers launched by the harness enforce:
- `--network none`: zero internet or local network connectivity.
- `--read-only`: root filesystem is mounted read-only.
- `--cap-drop ALL`: drops all Linux capabilities (cannot mount, ptrace, raw socket, etc.).
- `--security-opt no-new-privileges`: prevents `setuid` privilege escalation.
- `--user 1001:1001`: runs as an unprivileged, non-root user.
- Resource constraints (`--memory 1g`, `--pids-limit 256`, ulimits).

### Hardened Runtimes (Rootless, gVisor, Kata)

While `--network none --read-only --cap-drop ALL` provides strong isolation, standard runc containers share the host Linux kernel. In environments evaluating completely untrusted adversarial code at scale:

#### 1. Rootless Docker
Run the Docker daemon without root privileges using Rootless mode:
```bash
dockerd-rootless-setuptool.sh install
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock
```

#### 2. gVisor (`runsc`)
gVisor provides an application kernel written in Go between the containerized application and the host kernel.
Install `runsc` and configure `/etc/docker/daemon.json`:
```json
{
  "runtimes": {
    "runsc": {
      "path": "/usr/bin/runsc"
    }
  }
}
```
Launch with `--runtime=runsc` to intercept and sandbox all system calls.

---

## 3. Standardized Exit Codes

The CLI emits standardized exit codes for programmatic integration into CI/CD and evaluation pipelines:

| Exit Code | Identifier | Description |
|---|---|---|
| `0` | `RESOLVED` | All evaluated tasks passed every declared test and resolved successfully. |
| `1` | `FAILED` | Candidate code executed but failed one or more expected tests, or mutants survived. |
| `2` | `USAGE_ERROR` | Command line argument error, missing required task file, or task not found. |
| `3` | `TIMEOUT` | Evaluation exceeded the per-task timeout limit. |
| `4` | `TAMPERING_OR_VIOLATION` | Anti-cheat AST rule violation or report tampering detected. |

---

## 4. Observability, Structured Logging, and Metrics

### Structured JSON Logging

To pipe machine-readable logs into log aggregators (e.g. Datadog, CloudWatch, Loki):
```bash
python -m harness.cli run-all --verify-gold --log-json
```

Log records are emitted as single-line JSON objects to `stderr`:
```json
{
  "timestamp": "2026-10-26T15:39:15.996123Z",
  "level": "INFO",
  "logger": "harness",
  "message": "Task task_001_concurrent_lru_cache finished with status RESOLVED in 1.45s"
}
```

### Secret Redaction Guarantee

All logging handlers pass through `RedactingFilter`:
- 256-bit ephemeral HMAC session tokens (`[0-9a-fA-F]{64}`) are masked as `[REDACTED]`.
- API keys (`sk-ant-...`) are masked as `[REDACTED]`.
- Environment token variables (`_HARNESS_SECRET_TOKEN=...`, `ANTHROPIC_API_KEY=...`) are scrubbed before output.

### Metrics Collection

Pass `--metrics` to emit summary execution statistics:
```bash
python -m harness.cli run-all --verify-gold --metrics
```
Output:
```json
{
  "total_runs": 3,
  "resolved_runs": 3,
  "resolve_rate": 1.0,
  "status_counts": {
    "RESOLVED": 3
  },
  "durations": {
    "min_seconds": 1.42,
    "mean_seconds": 2.11,
    "max_seconds": 3.05,
    "total_seconds": 6.33
  }
}
```

---

## 5. Task Maintenance & Upgrade Policies

When introducing a new benchmark task or upgrading existing tests:

1. **Follow [ADDING_TASKS.md](ADDING_TASKS.md)**:
   - Provide `problem.md`, `baseline.py`, `solution.py` (or `solution.patch`), and `test_task.py`.
2. **Derive F2P / P2P Tests**:
   - Run `python -m harness.cli calibrate <task_id> --repeat 3 --write`.
   - Never manually hand-edit FAIL_TO_PASS or PASS_TO_PASS lists without calibration.
3. **Write Targeted Mutants**:
   - Every common shortcut or hurried mistake a language model makes must have a corresponding mutant in `task.json`.
4. **Enforce 100% Mutant Kill Rate**:
   - Run `python -m harness.cli verify <task_id> --repeat 3`.
   - The task is rejected unless mutation score is 100% and verdict is `VALID`.
