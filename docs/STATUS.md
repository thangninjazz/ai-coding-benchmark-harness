# Project Status and Architecture Overview

What has been verified, what has been hardened, and operations guide.

## Verified (by running it)

| Check | Where it was run | Result |
|---|---|---|
| **Section 1 Exploit Reproduction** | Local environment | Verified reproducible: baseline forged JUnit report when static scan was bypassed |
| **Verdict Isolation Architecture** | Local environment | Mitigated: HMAC-authenticated event stream with ephemeral nonces (`harness/reporter.py`) |
| **Red-Team Exploit Corpus** | Local environment | **37/37 tests pass** (`tests/redteam/`) with static scan disabled |
| **Fast self-tests, ruff, mypy** | Local, Python 3.12 | All clean; mypy 0 issues in 12 source files |
| **Coverage Gate (>= 85%)** | Local, Python 3.12 | **93% overall coverage** (evaluator: 90%, reporter: 88%, observability: 94%) |
| **Provenance & Schema Validation** | Local, Python 3.12 | `tests/test_provenance.py` validates `result.schema.json`, task content hash & reproducibility |
| **Structured Logging & Exit Codes** | Local, Python 3.12 | `tests/test_observability.py` validates secret redaction, metrics collection, and exit codes (0-4) |
| **Benchmark Validity (`verify`)** | Local & Linux | `task_001` (21/21 mutants killed, VALID), `task_003` (34/34 mutants killed, VALID), `task_002` (16/16 mutants killed) |
| **Docker container limits & cleanup** | Docker backend | Container lifecycle tracking, atexit cleanup, ulimits enforced |
| **CI Workflow Hardening** | `.github/workflows/ci.yml` | 8 dedicated jobs; actions pinned by SHA; pip-audit; Trivy container scan; CodeQL; SBOM |

## Hardened Components

1. **Verdict Integrity**:
   - Replaced untrusted `--junitxml` file output with HMAC-signed streaming reporter (`_harness_reporter.py`).
   - 256-bit ephemeral secret tokens are popped and scrubbed from `os.environ` and `sys.argv` before candidate code is imported.
   - Raw `call.excinfo` is inspected directly, defeating pytest monkeypatching.
   - Premature exits (`os._exit`, `sys.exit`) fail stream termination checks and result in `TestStatus.ERROR`.
2. **Container Sandbox**:
   - Enforced `--ulimit nofile=256:256`, `--ulimit nproc=128:128`, `--ulimit fsize=16777216:16777216`, `--ulimit core=0:0`.
   - Guaranteed cleanup of active container IDs on normal exit, timeout, crash, or `SIGINT`.
3. **Machine-Readable Provenance**:
   - `BenchmarkResult` contains `Provenance` metadata: git SHA, image digest, task content SHA-256 hash, python version, backend, timeout, seed, and timestamp.
   - JSON schema validated in `harness/schema/result.schema.json`.
4. **Production Observability**:
   - Structured JSON logging (`--log-json`) with automatic secret redaction (`[REDACTED]`).
   - `MetricsCollector` (`--metrics`) tracking resolve rates, status counts, and duration statistics.
   - Standardized exit codes (`0=RESOLVED`, `1=FAILED`, `2=USAGE_ERROR`, `3=TIMEOUT`, `4=TAMPERING_OR_VIOLATION`).

## Documentation Map

| Read this | For |
|---|---|
| [README.md](../README.md) | What it is, quickstart, task descriptions, Docker isolation, limitations |
| [docs/DESIGN_VERDICT.md](DESIGN_VERDICT.md) | Architecture evaluation and defense mechanism for verdict generation |
| [docs/OPERATIONS.md](OPERATIONS.md) | Sizing, concurrency, container security (rootless/gVisor), exit codes, metrics |
| [docs/THREAT_MODEL.md](THREAT_MODEL.md) | Security threat boundaries, trust models, and out-of-scope risks |
| [docs/ADDING_TASKS.md](ADDING_TASKS.md) | Creating single-file and patch tasks, calibration, and mutant verification |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Development setup and submission criteria |
