# Adding a task

A task is a directory `tasks/task_NNN_short_name/` containing:

| File | Purpose |
|---|---|
| `problem.md` | The specification shown to the model. Every behaviour the tests check must be stated here. |
| `baseline.py` | A realistic *wrong* answer - the kind of code a hurried model writes. |
| `solution.py` | The reference ("gold") answer. |
| `test_task.py` | pytest tests; `from implementation import ...` (the harness copies the candidate to `implementation.py`). |
| `task.json` | Metadata, import allowlist, FAIL_TO_PASS / PASS_TO_PASS, mutants. |

## `task.json`

```json
{
  "title": "...",
  "difficulty": "Hard",
  "timeout_seconds": 90,
  "allowed_imports": ["collections", "threading", "time", "typing"],
  "fail_to_pass": ["..."],
  "pass_to_pass": ["..."],
  "mutants": [
    {
      "name": "wall_clock",
      "reason": "uses time.time() instead of time.monotonic()",
      "edits": [{ "find": "time.monotonic()", "replace": "time.time()", "count": 3 }]
    }
  ]
}
```

* `fail_to_pass` - tests that **fail on the baseline** and pass on gold (the "bug"). 
* `pass_to_pass` - tests that pass on both (behaviour that must not regress).
* `allowed_imports` - the only modules the candidate may import (dotted prefixes).
* `mutants` - literal text edits applied to `solution.py`. `find` must occur exactly
  `count` times (default 1), so a refactor of the gold solution that breaks a mutant is
  reported rather than silently ignored.

## Workflow

```bash
python -m harness.cli calibrate task_NNN_name --repeat 5 --write   # derive F2P / P2P
python -m harness.cli verify task_NNN_name --repeat 3              # must say VALID
```

`verify` is the acceptance test for a task. It fails if any of these is false:

1. gold and baseline pass the anti-cheat scan;
2. gold resolves in every repetition (no flaky test);
3. baseline is unresolved, every FAIL_TO_PASS test fails on it and every PASS_TO_PASS test passes;
4. **every mutant is killed in every repetition** (mutation score 100%).

## Rules that keep a task honest

* **Spec = tests = gold.** If a test asserts it, `problem.md` says it, and gold does it.
  A model should never be failed for a behaviour the prompt left open.
* **Make wrong answers fail for the right reason.** Write a mutant for each mistake you
  expect (wrong clock, missing lock, off-by-one, unvalidated input...). A mutant that
  survives means a missing or too-weak test; an *equivalent* mutant (same behaviour) must
  be dropped, not tolerated - for example, removing an `asyncio.Lock` around code with no
  `await` inside does not change behaviour.
* **No sleeps for correctness.** Drive time with a fake clock where you can
  (`monkeypatch.setattr(time, "monotonic", ...)`). Where real time is unavoidable, assert
  *bounds computed from measured timestamps* (tokens cannot accrue faster than the rate),
  not fixed durations.
* **Concurrency tests must be deterministic on gold and probabilistic only for bugs.**
  Use `sys.setswitchinterval(1e-6)`, a `Barrier`, a shared key space, and assert
  invariants (no exceptions, never over capacity, values match keys). `verify --repeat`
  measures how reliably they catch the racy mutants.
* Guard anything that could hang with `asyncio.wait_for(...)` / `thread.join(timeout)` so
  a bug fails the test instead of consuming the whole timeout.
