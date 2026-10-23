# AI Coding Benchmark Harness

[![CI](https://github.com/thangninjazz/ai-coding-benchmark-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/thangninjazz/ai-coding-benchmark-harness/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An offline harness for **authoring, validating and scoring** coding-benchmark tasks for
language models, using SWE-bench-style scoring (`FAIL_TO_PASS` / `PASS_TO_PASS`, a task
is *resolved* only if every expected test passes) and an unbiased **pass@k** estimator.
It supports single-file implementation tasks and **multi-file patch tasks**, where the
model submits a unified diff against a repository snapshot, as in SWE-bench.

Its distinguishing feature is that it treats the *benchmark itself* as something to test.
`harness verify` proves that each task can tell good code from bad: the reference solution
must pass, the buggy baseline must fail exactly where declared, and **every one of a set of
plausible wrong solutions ("mutants") must be rejected** - repeated several times to rule
out flaky tests.

## What is measured, and what is not

| Claim | Evidence |
|---|---|
| Reference solutions resolve all three tasks | `harness run-all --verify-gold` (CI, every push) |
| Baselines are genuinely wrong | `verify`: every FAIL_TO_PASS test fails on the baseline, every PASS_TO_PASS test passes |
| Tests are sharp | `verify`: **71 hand-written wrong solutions, 71 killed** (21 for task 001, 16 for task 002, 34 for task 003) |
| Tests are not flaky | `verify --repeat 3` in CI; the concurrency tests kill the racy mutants reliably and never fail gold |
| Sandbox actually isolates | integration tests against a real container: no network, read-only submission, kill-on-timeout leaves no container behind |

Not claimed: no frontier-model pass rates are published here. The tasks are designed so
that the *typical mistakes* of a hurried answer (wall clock, missing lock, no `await`
discipline, unvalidated edge cases) fail, and that is verified against mutants - but
whether a given model makes those mistakes is an empirical question the `score` command
exists to answer.

## How a submission is judged

```
 candidate source
        |
        v
 [1] static scan ............ per-task import allowlist + AST rules   (anti_cheat.py)
        |
        v
 [2] sandbox run ............ pytest in a fresh work dir              (sandbox.py)
        |                      - local: scrubbed env, rlimits, process-tree kill
        |                      - docker: --network none, read-only, cap-drop ALL, limits
        v
 [3] integrity checks ....... JUnit report must parse; test ids must equal the declared
        |                      set exactly; workspace files must be untouched; exit code
        |                      must agree with the report                (evaluator.py)
        v
 [4] verdict ................ RESOLVED only if every FAIL_TO_PASS and PASS_TO_PASS passed
```

The process exit code alone is never trusted: a candidate that calls `os._exit(0)` before
the tests run produces *no report* and is an `ERROR`, not a pass. See
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for what each layer defends against and the
limits that remain.

## The tasks

| Task | Format | Core difficulty | Tests (F2P + P2P) | Mutants |
|---|---|---|---|---|
| [001 - Thread-safe LRU cache with ms TTL](tasks/task_001_concurrent_lru_cache/problem.md) | single file | exact TTL boundary semantics, *expired-before-LRU* eviction in `O(log n)` (lazy-deletion heap), real thread contention | 14 + 10 | 21 |
| [002 - Async token bucket with monotonic drift compensation](tasks/task_002_async_token_bucket_limiter/problem.md) | single file | monotonic vs wall clock, re-measuring after every sleep, atomic check-and-deduct across `await`, cancellation, no event-loop blocking | 11 + 14 | 16 |
| [003 - Cart pricing: rounding, coupon stacking, per-line tax](tasks/task_003_cart_pricing_patch/problem.md) | **unified diff, 4 files** | half-up vs banker's rounding, `Decimal` vs `float`, a largest-remainder allocator that never loses a cent, coupon ordering and capping, tax on discounted lines | 47 + 14 | 34 |

Each `problem.md` states every behaviour the tests check, and the tests, the spec and the
reference solution agree with one another - a model is never failed for something the
prompt left open. Examples of what the suites catch (all as verified mutants): `time.time()`
instead of `time.monotonic()`; a lock-free `get()`; a check-then-`await`-then-deduct race;
`size()` counting expired entries; a request larger than capacity that hangs; a
`time.sleep` inside a coroutine; banker's rounding; tax charged on pre-discount amounts; a
fixed coupon split evenly instead of proportionally; leftover cents dropped by the allocator.

Task 003 is scored the way SWE-bench scores a patch: the model's diff is applied to a
pristine copy of `repo/` (only `.py` files under `shop/`; no renames, symlinks, traversal or
`conftest.py`), the hidden tests are dropped in, and the task is resolved only if all
`FAIL_TO_PASS` and `PASS_TO_PASS` tests pass. Fixing one of the four files is not enough.

## Quickstart

```bash
git clone https://github.com/thangninjazz/ai-coding-benchmark-harness.git
cd ai-coding-benchmark-harness
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m harness.cli list
python -m harness.cli run-all --verify-gold             # reference solutions -> resolved
python -m harness.cli run-all                           # baselines -> not resolved
python -m harness.cli verify --repeat 3                 # test the tests (gold / baseline / mutants)
pytest -m "not slow and not docker"                     # harness self-tests
```

Evaluate a model's answer, and score several samples:

```bash
python -m harness.cli run task_001_concurrent_lru_cache --candidate my_answer.py
python -m harness.cli run task_003_cart_pricing_patch --candidate my_fix.diff   # patch tasks take a diff
python -m harness.cli score task_001_concurrent_lru_cache s1.py s2.py s3.py s4.py -k 1 -k 4
#   pass@k = 1 - C(n-c, k) / C(n, k)   (Chen et al., 2021)
```

## Docker

The image is built in two stages: dependencies are resolved to wheels in a throw-away stage
and installed offline into the runtime image, which contains no compiler, `git`, `curl` or
pip cache. The harness code is owned by root and the process runs as unprivileged uid 1001.

```bash
docker build -t ai-benchmark-harness:latest .

# Network isolation is a property of `docker run`, not of the image - always pass it:
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --memory 1g \
  --tmpfs /tmp:rw,size=128m \
  ai-benchmark-harness:latest run-all --verify-gold
```

Or keep the harness on the host and run only the *untrusted code* in a throw-away
container (this is what you want for model output):

```bash
python -m harness.cli --backend docker run task_002_async_token_bucket_limiter --candidate my_answer.py
```

`DockerSandbox` always applies `--network none --read-only --cap-drop ALL --pids-limit 256
--memory 512m --cpus 1 --user 1001:1001`, mounts the submission read-only and kills and
removes the container on timeout.

## Anti-cheat rules

Findings carry a rule id and a line number, e.g. `line 3: [AC003] forbidden runtime object: sys.modules`.

| Rule | Detects |
|---|---|
| AC001 / AC002 | forbidden module (`pytest`, `unittest`, `importlib`, `os`, `subprocess`, `socket`, `inspect`, ...) / import outside the task's allowlist |
| AC003 | forbidden runtime objects, resolved through aliases: `sys.modules`, `sys._getframe`, `sys.exit`, `os._exit`, `os.kill`, ... (`import sys as s`, `from sys import modules` and `m = sys` are all caught) |
| AC005 | object-model escapes: `__builtins__`, `__globals__`, `__subclasses__`, `f_back`, ... |
| AC006 / AC007 | `exec`, `eval`, `compile`, `__import__`, `open`, `exit`, ...; dynamic `getattr` |
| AC008 | `__eq__` / `__bool__` that return a constant (impersonating any value); attribute-interception dunders |
| AC009 | executable statements at import time (code must only *define* things) |

This is a defence-in-depth layer, not a security boundary - see the threat model. Anything
that slips past it still has to survive the report-integrity checks and, in Docker, a
container with no network and a read-only filesystem.

## Repository layout

```
harness/
  anti_cheat.py   AST rules (alias-resolving visitor, per-task allowlist)
  sandbox.py      local and Docker execution backends
  workspace.py    stages a candidate (source or unified diff) into a work dir; strict patch applier
  evaluator.py    stage + scan -> run -> integrity checks -> verdict
  verify.py       mutation-based task validation, F2P/P2P calibration
  metrics.py      unbiased pass@k
  models.py       pydantic schemas (task.json, results)
  cli.py          list | run | run-all | verify | calibrate | score
tasks/            problem.md, test_task.py, task.json + baseline.py/solution.py, or repo/ + solution.patch
tests/            harness self-tests (unit, evaluator integrity, Docker integration)
docs/             THREAT_MODEL.md, ADDING_TASKS.md
```

Adding a task: [docs/ADDING_TASKS.md](docs/ADDING_TASKS.md).

## Known limitations

* The patch task is a small synthetic library written for this repository, not a bug mined
  from a real project's history. It exercises the SWE-bench *mechanics* (diff in, hidden
  tests, F2P/P2P) at a far smaller scale than SWE-bench's real repositories.
* No frontier-model results are included (none were run); `score` exists to produce them.
* Candidate code and tests run in one interpreter (as in any pytest benchmark); see the
  threat model for what that implies.
* Timing tests use real time. They assert bounds derived from measured timestamps and are
  re-run by `verify --repeat`, but they are still real-time tests.
* The test suite and reference solutions were run on Windows 11 / Python 3.13 and on Linux
  (Debian) with Python 3.10, 3.11 and 3.12. The GitHub Actions workflow is provided, but its
  first run on GitHub had not been observed when this was written.

## License

MIT - see [LICENSE](LICENSE). Author: Do Van Thang ([@thangninjazz](https://github.com/thangninjazz)).
