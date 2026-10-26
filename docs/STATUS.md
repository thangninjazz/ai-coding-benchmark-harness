# Project status and next steps

What has been verified, what has not, and how to pick the work up on another machine.

## Verified (by running it)

| Check | Where it was run |
|---|---|
| Fast self-tests, ruff, mypy, coverage >= 85% (about 94%) | Windows 11, Python 3.13 |
| Same suite, all 3 reference solutions resolve | Linux (Debian) in Docker, Python 3.10, 3.11, 3.12 |
| `verify --repeat 3`: gold resolves 3/3, baseline fails exactly the declared tests, **71/71 mutants killed** (21 + 16 + 34) | Linux, Python 3.12 |
| Docker backend: no network, read-only submission, kill-on-timeout leaves no container | real container |
| Locked-down container run of all reference solutions (`--network none --read-only --cap-drop ALL`) | real container |

## Not done

| Item | Why | How to do it |
|---|---|---|
| **First run of the GitHub Actions workflow** | the repo could not be pushed from the machine it was built on (HTTP 503 on the company network) | push, open the Actions tab, fix whatever is red. Likely suspects: timing-sensitive tests on slow runners, the `docker` job |
| **Pass rates for real models** | no model was run; it costs money and was not authorised | `python -m harness.cli eval-model --provider anthropic --model <id> -n 10 -k 1 -k 5 --yes` (see README, "Measuring a model") |
| **Tests run from a separate trusted process** | candidate code and tests share one interpreter; a determined attacker who knows about the canary can patch around it ([THREAT_MODEL.md](THREAT_MODEL.md)) | a different architecture: collect results from a process the candidate never imports into |
| **A patch task mined from a real bug** | task 003 is a small synthetic library | pick a real, small, pure-Python bug with a regression test; follow [ADDING_TASKS.md](ADDING_TASKS.md) ("Patch tasks") |
| **A fourth task** | two ideas worth doing: a write-ahead-log key-value store with crash recovery (kill mid-write, torn writes, fsync/rename ordering), and a second real-bug patch task | `calibrate --write`, then `verify --repeat 3` must say VALID |
| POSIX-only tests on Windows | resource limits and symlink tests skip there | they run in CI on Linux |

## Documentation map

| Read this | For |
|---|---|
| [README.md](../README.md) | what it is, quickstart, the tasks, Docker, anti-cheat rules, limitations |
| [docs/ADDING_TASKS.md](ADDING_TASKS.md) | writing a single-file or patch task, mutants, calibrate/verify workflow |
| [docs/THREAT_MODEL.md](THREAT_MODEL.md) | what each defence stops and what it does not |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | setup, what a change must include |
| `python -m harness.cli --help` | every command and flag |

## Moving the repository to another machine

Git history is the valuable part, so move the history, not just the files.

**Option A, a git bundle (cleanest).** One file containing every commit:

```bash
# on this machine, inside the repo
git bundle create ai-coding-benchmark-harness.bundle --all
git bundle verify ai-coding-benchmark-harness.bundle

# on the other machine
git clone ai-coding-benchmark-harness.bundle ai-coding-benchmark-harness
cd ai-coding-benchmark-harness
git remote set-url origin https://github.com/thangninjazz/ai-coding-benchmark-harness
git config user.email "thangninjazz@gmail.com"      # per-repo: do not use a work address
git config user.name  "Do Van Thang"
git push origin main
```

**Option B, zip the folder.** Include the hidden `.git` directory or the history is lost, and
leave out caches and archives. `.git/config` (the remote URL and the local author email) travels
with it, so check `git config user.email` on arrival.

**Then, on the new machine:**

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
make lint test                                         # or: python -m pytest -m "not slow and not docker"
python -m harness.cli verify --repeat 3                # the real acceptance test
```

Docker is optional (`make docker-test`). The `anthropic` package is only needed for
`eval-model --provider anthropic`.
