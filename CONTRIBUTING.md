# Contributing

Thanks for taking a look. The bar for this repository is that **every claim is backed by a
check that can fail**, so most contributions are one of three things.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                     # or: make install
make lint test                                          # ruff + mypy + fast tests (85% coverage floor)
```

Docker is optional: `make docker-test` builds the image and runs the container-backed tests,
which skip themselves when the image is absent.

## Adding or changing a task

Read [docs/ADDING_TASKS.md](docs/ADDING_TASKS.md). A task is accepted only when
`python -m harness.cli verify <task> --repeat 3` reports **VALID**:

* the reference solution resolves it every time;
* the baseline fails exactly the declared `FAIL_TO_PASS` tests and passes the `PASS_TO_PASS` ones;
* **every mutant (a plausible wrong answer) is killed, every time.** A surviving mutant is a
  missing or weak test, not a flaky one: fix the test.

When you change a test file, re-run `calibrate --write` so `task.json` matches it, then `verify`.

## Changing the harness

* A change to the verdict path (`evaluator.py`, `workspace.py`, `sandbox.py`, `anti_cheat.py`)
  needs a test that *fails without the change*. For cheats, add the cheat to
  `tests/test_anti_cheat.py` (static) or `tests/test_evaluator.py` (runtime).
* Anything that touches POSIX-only code (`resource`, process groups, symlinks) is exercised in
  CI on Linux; Windows skips those tests.
* Keep `docs/THREAT_MODEL.md` honest: if a change weakens or strengthens a defence, say so there.

## Style

`ruff` and `mypy` must pass. Match the surrounding code; comments explain *why*, not *what*.
