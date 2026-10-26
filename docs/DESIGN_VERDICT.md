# Verdict Isolation Architecture Design

## 1. Problem Statement & Vulnerability Analysis

In the original harness architecture, test execution and verdict collection suffered from two fundamental vulnerabilities when the static AST scan was disabled or bypassed:

1. **Filesystem Report Forgery (`sys.argv` / `/out/junit.xml`)**:
   Pytest was invoked with `--junitxml=<path>`, where `<path>` pointed to a file inside the output directory. Because candidate code runs within pytest and has write access to disk, an adversarial submission could:
   - Extract the path directly from `sys.argv` (`[a.split("=", 1)[1] for a in sys.argv if a.startswith("--junitxml=")]`);
   - Or write directly to known/guessed paths (`/out/junit.xml`, `../bench-out-*/junit.xml`);
   - Write a forged XML declaring all expected test cases as passed and the canary as failed;
   - Immediately execute `os._exit(1)`.
   Because the supervisor trusted the file on disk and checked only that the exit code was 1, a completely broken baseline was marked `TestStatus.RESOLVED`.

2. **In-Process Reporting Monkeypatching**:
   Because candidate code executes in the same Python runtime as pytest, candidate code could monkeypatch `_pytest.reports.TestReport.from_item_and_call`. By selectively forcing `rep.outcome = "passed"` for all non-canary tests while letting the canary fail, pytest itself generated a forged JUnit XML report on disk without triggering canary integrity alarms.

---

## 2. Evaluation of Architectural Options

### Option A: Candidate in Separate Process over RPC Boundary
- **Description**: The pytest test suite runs in a trusted supervisor process. Candidate code runs in a distinct child process. Test cases interact with candidate classes/functions over an IPC/RPC boundary (e.g., domain socket, multiprocessing queue, or JSON-RPC).
- **Pros**: Strong memory and namespace isolation. Candidate code cannot access pytest objects, monkeypatch runner classes, or tamper with test execution data.
- **Cons & Costs**:
  - Extremely high cost for existing benchmark tasks: Existing tasks (`task_001_concurrent_lru_cache`, `task_002_async_token_bucket_limiter`, `task_003_cart_pricing_patch`) rely on in-memory Python semantics.
  - Task 001 benchmarks concurrent multi-threaded contention using `threading.Lock` and direct method calls across multiple threads.
  - Task 002 benchmarks `asyncio` event-loop scheduling, timeouts, and cancellation. Proxies over RPC cannot faithfully preserve in-memory thread synchronization or asyncio task scheduling.
  - Would require completely rewriting all benchmark tasks into client-server protocols, destroying the benchmark's compatibility with standard SWE-bench evaluation conventions.

### Option B: Privilege Separation (Separate UID / Runner Container)
- **Description**: Candidate code executes under an unprivileged user (UID 1001), while verdict collection and pytest orchestration run under a separate trusted UID or supervisor process. Results travel over an isolated channel.
- **Pros**: Strong OS-level separation on Linux/Docker.
- **Cons**: If pytest itself runs the tests, candidate code must still be loaded by pytest in Python unless tests are external. Inside the container, pytest and candidate still run as the unprivileged user unless a multi-process architecture is used.

### Option C: Authenticated Out-of-Band Streaming Protocol with Ephemeral Secrets (Chosen Architecture)
- **Description**:
  1. **Eliminate File-Based JUnit**: Remove `--junitxml` from `sys.argv`. The runner never writes a verdict report file to disk, and the candidate cannot discover or overwrite any report file.
  2. **Ephemeral Secret Challenge-Response**: The supervisor generates an unguessable 256-bit cryptographic session token (`session_token`) and derives HMAC nonces for test lifecycle events.
  3. **Early Secret Scrubbing**: The session secret is provided to the runner via an early-stage channel and immediately scrubbed from `sys.argv` and `os.environ` before any candidate code or test file is loaded.
  4. **Direct Protocol Streaming**: An internal pytest plugin hooks `pytest_runtest_protocol` and `pytest_runtest_makereport` directly. Instead of reading `TestReport.outcome` (which can be monkeypatched), it directly inspects `call.excinfo` and streams signed JSON event records (`test_start`, `test_finish`, `session_finish`) to the supervisor over an anonymous unidirectional channel (stdout/stderr structured protocol).
  5. **Fail-Closed Verification**: The supervisor verifies cryptographic HMAC signatures for every test event. If the process terminates early (`os._exit`), missing test events immediately cause `TestStatus.ERROR`. If the signature is invalid or absent, the event is rejected as tampering.

---

## 3. Comparison Summary

| Metric / Requirement | Option A: RPC Process Boundary | Option B: Multi-UID Separation | Option C: Authenticated Out-of-Band Stream (Chosen) |
|---|---|---|---|
| **Defeats File Forgery** | Yes | Yes | Yes (No file on disk, no `--junitxml`) |
| **Defeats In-Process Monkeypatching** | Yes | Partial | Yes (Direct `excinfo` inspection + HMAC signing) |
| **Defeats Early `os._exit`** | Yes | Yes | Yes (Strict event stream completion validation) |
| **Supports Multi-threaded & Async Tasks** | Poor (RPC destroys in-memory concurrency) | Good | Full (Preserves native Python semantics) |
| **Implementation Complexity** | Massive rewrite of all tasks & tests | High (Multi-UID container plumbing) | Low-Medium (Harness runner & evaluator) |
| **Docker / Local Compatibility** | Linux & Windows | Linux/Docker only | Linux & Windows |

---

## 4. Guarantees and Honest Boundaries

### What We Guarantee:
1. **No Filesystem Attack Surface**:
   The harness never passes `--junitxml` or points pytest to any writable verdict file. In `DockerSandbox`, no output directory is mounted writable by candidate code.
2. **Early Termination is Not a Pass**:
   Calling `os._exit(0)`, `os._exit(1)`, or raising fatal signals before all tests genuinely complete results in an immediate failure (`TestStatus.ERROR: premature termination or missing test events`).
3. **Unforgeable Event Stream**:
   Every test result record requires an HMAC signature keyed by a 256-bit session nonce. Submissions cannot forge event records without knowing the ephemeral nonce.
4. **Resilience to Standard Monkeypatching**:
   The reporter plugin hooks `pytest_runtest_protocol` and verifies `call.excinfo` directly, rendering monkeypatching of `_pytest.reports.TestReport.from_item_and_call` ineffective.

### What We Explicitly Do NOT Guarantee (Honest Boundaries):
1. **In-Process Memory Introspection**:
   Because candidate code and pytest execute within the same Python virtual machine, an adversary with arbitrary Python code execution could theoretically scan heap objects using `gc.get_objects()` to hunt for the 256-bit nonce in memory. While we scrub the nonce from `sys.argv`, `os.environ`, and module globals, Python does not provide an airtight hardware security boundary against in-process heap scanning.
   > If untrusted, hostile code must be evaluated in production, the static AST scan remains essential defense-in-depth, and execution must occur inside `DockerSandbox` with `--network none`, `--read-only`, non-root user, and resource limits.
2. **LocalSandbox Boundary**:
   `LocalSandbox` is strictly for development and local testing. It shares the host kernel and filesystem. Only `DockerSandbox` provides containerized isolation.
