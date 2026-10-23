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

The process exit code is **not** trusted. A run is `RESOLVED` only if all of these hold:

1. the static scan found nothing;
2. pytest wrote a JUnit report that parses;
3. the set of test ids in the report equals exactly the `FAIL_TO_PASS + PASS_TO_PASS`
   declared in `task.json` (deleted, added or uncollected tests are an `ERROR`);
4. every one of them is `passed` (a skip is not a pass);
5. the exit code agrees with the report;
6. the work directory is byte-identical, file for file, to what the harness staged: nothing
   was created, removed or modified while the tests ran (this covers `conftest.py`, the
   tests, `pytest.ini` and the candidate's own sources).

Plugin auto-loading is disabled (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) and the two required
plugins are named explicitly, so a stray pytest plugin on the host cannot change results.

## Honest limits

* **The AST scan is defense in depth, not a boundary.** Python is too dynamic for a static
  filter to be airtight (for example, constructing a forbidden name from data the scan
  cannot see). The scan raises the cost of casual cheating and catches what language
  models actually do; anything it misses must still survive the integrity checks above,
  and the container prevents it from touching anything outside the run.
* **Candidate code and tests share one interpreter.** That is how every pytest-based
  benchmark works. A sufficiently determined candidate can, in principle, tamper with the
  in-process pytest session. The JUnit/test-id/exit-code cross-checks make silent success
  unlikely, but they do not make it impossible. The remedy for adversarial settings is to
  run the tests from a separate, trusted process; that is a different architecture and is
  listed as future work.
* **The `local` backend is a development convenience.** It scrubs the environment, applies
  POSIX resource limits and kills the process tree on timeout, but it shares the host
  kernel, filesystem and network. Use `--backend docker` for untrusted code.
* **Docker cannot bake network isolation into an image.** `--network none` is a flag on
  `docker run`; `DockerSandbox` always passes it, and the README shows it for manual runs.
* Timing-based tests use real time. They are written as bounds derived from measured
  timestamps so they do not depend on machine speed, and `verify --repeat N` is the guard
  against residual flakiness - but they are still real-time tests.
