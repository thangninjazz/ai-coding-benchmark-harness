# Threat model

The harness runs code written by a language model and decides whether it "solved" a
task. Two different things can go wrong, and they need different defences.

| Threat | Example | Defence | Layer |
|---|---|---|---|
| **Gaming the verdict** | exit with status 0 before tests run; patch `sys.modules`; write `conftest.py`; edit `test_task.py`; return an object whose `__eq__` is always true | per-task import allowlist + AST rules; JUnit integrity checks; workspace hash check | `anti_cheat.py`, `evaluator.py` |
| **Attacking the host** | read `~/.ssh`, exfiltrate tokens, open sockets, fork-bomb, fill the disk | container: no network, read-only root, dropped capabilities, PID / memory / CPU limits, non-root user | `DockerSandbox` |
| **Denial of service** | `while True`, deadlock, huge allocation | wall-clock timeout with process-tree kill; per-test timeout; rlimits | both backends |
| **Hostile patch** (patch tasks) | a diff that edits the tests, adds `conftest.py`, escapes with `../`, uses symlinks or renames, or touches non-Python files | strict pure-Python patch applier with an editable-path allowlist; static scan of every touched file; whole-tree hash compared before and after the run | `workspace.py`, `evaluator.py` |
| **Invalid benchmark** | tests that pass on buggy code, flaky tests, tests nothing can pass | `verify`: gold must resolve, baseline must fail, every mutant must die, repeated N times | `verify.py` |

## What the verdict is based on

The process exit code and filesystem files are **not** trusted alone. A run is `RESOLVED` only if all of these hold:

1. the static scan found nothing (rules AC001-AC009 + per-task import allowlist);
2. test execution events travel over an authenticated, signed out-of-band stream (no writable
   `junit.xml` file on disk, no `--junitxml` on `sys.argv`);
3. every test event carries a valid HMAC signature matching the run's ephemeral 256-bit session secret;
4. the set of reported test ids equals exactly the `FAIL_TO_PASS + PASS_TO_PASS` declared in `task.json`
   (deleted, added, uncollected, or prematurely terminated tests are an `ERROR`);
5. every expected test genuinely passed (`call.excinfo` was None during test execution);
6. the **canary** test that the harness adds to every run and that always fails is reported as
   failed, and pytest exits with 1 (so a healthy run can never exit 0);
7. the work directory is byte-identical, file for file, to what the harness staged: nothing
   was created, removed or modified while the tests ran (this covers `conftest.py`, the
   tests, `pytest.ini` and the candidate's own sources).

Plugin auto-loading is disabled (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) and the required plugins
are loaded explicitly, so stray pytest plugins on the host cannot change results.

## Honest limits

* **The AST scan is defense in depth, not a boundary.** Python is too dynamic for a static
  filter to be airtight (for example, constructing a forbidden name from dynamic expressions).
  The scan raises the cost of casual cheating and catches what language models actually do;
  anything it misses must still survive the runtime integrity and cryptographic streaming checks,
  and the container prevents it from touching anything outside the run.
* **In-process test execution and memory boundaries.** Existing benchmark tasks benchmark native
  in-memory synchronization (`threading.Lock` in Task 001, `asyncio` event loops in Task 002),
  which requires running tests and candidate code in the same Python process. While we eliminate
  filesystem reports (`--junitxml`) and verify test events via cryptographic HMAC signatures with
  secrets scrubbed from `sys.argv` and `os.environ` before candidate code loads, Python does not
  provide hardware-level memory protection against intra-process heap inspection (e.g., via
  `gc.get_objects()`). For truly hostile, untrusted submissions, the AST scan and the container
  isolation (`DockerSandbox` with `--network none --read-only --cap-drop ALL`) must always be used
  together as defense in depth. See `docs/DESIGN_VERDICT.md` for architectural comparisons.
* **The `local` backend is a development convenience.** It scrubs the environment, applies
  POSIX resource limits and kills the process tree on timeout, but it shares the host
  kernel, filesystem and network. Use `--backend docker` for untrusted code.
* **Docker cannot bake network isolation into an image.** `--network none` is a flag on
  `docker run`; `DockerSandbox` always passes it, and the README shows it for manual runs.
* Timing-based tests use real time. They are written as bounds derived from measured
  timestamps so they do not depend on machine speed, and `verify --repeat N` is the guard
  against residual flakiness - but they are still real-time tests.
