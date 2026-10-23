# AI Coding Benchmark & Evaluation Harness

[![CI Test & Evaluation Suite](https://github.com/thangninjazz/ai-coding-benchmark-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/thangninjazz/ai-coding-benchmark-harness/actions)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-hermetic%20sandbox-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

A reproducible, hermetic testbed designed for authoring, stress-testing, and evaluating **frontier AI coding models** (e.g., Claude 3.5 Sonnet, GPT-4o, Cursor, OpenAI Codex) under **SWE-bench** and **Terminal-bench** evaluation standards.

---

## 🎯 Motivation & Architecture

Modern AI coding agents frequently succeed on standard textbook problems but fail subtly when confronted with:
1. **Concurrency and race conditions** under high contention.
2. **Hidden state mutations** and circular reference cycles.
3. **Lazy shortcut implementations** (e.g., hardcoding mock values or bypasses).

This harness provides an isolated, offline evaluation environment that separates valid algorithmic solutions from hallucinated or fragile model-generated patches.

```
                      +-----------------------------+
                      | Candidate Patch / AI Output |
                      +-----------------------------+
                                     |
                                     v
                 [ AST Anti-Cheat & Integrity Inspection ]
                 * Checks forbidden mocks & monkeypatching
                                     |
                                     v  (Passed)
                   +----------------------------------+
                   | Hermetic Container / Environment |
                   |  - Pinned Toolchain & Libraries  |
                   |  - Zero Network (No Web Leakage) |
                   +----------------------------------+
                                     |
                                     v
                     [ Automated Pytest Test Harness ]
                     - Unit & Regression Checks
                     - Concurrency Stress Testing
                     - Deterministic Pass@1 Metrics
```

---

## 🚀 Key Features

* 🔒 **Hermetic Offline Execution:** All task evaluations execute in isolated sandboxes with zero external network connectivity, ensuring reproducible offline builds.
* 🛡️ **AST-Based Anti-Cheat Engine:** Static AST parsing detects test-suite monkeypatching (`sys.modules` injection, dynamic overrides) and unauthorized mock usage.
* ⏱️ **Strict Execution Timeouts:** Subprocess sandboxes prevent runaway loops and infinite blocking calls.
* 📊 **Automated Pass@1 Scoring:** Computes exact test coverage, assertion failures, and duration breakdowns.
* 📦 **Docker Ready:** Includes a multi-stage, unprivileged runner container for continuous evaluation pipelines.

---

## 📂 Repository Structure

```
├── .github/workflows/ci.yml       # Automated GitHub Actions test pipeline
├── harness/                       # Core evaluation engine
│   ├── anti_cheat.py              # AST-based static bypass detection
│   ├── cli.py                     # Command-line interface
│   ├── evaluator.py               # Sandboxed subprocess runner
│   └── models.py                  # Pydantic data schemas
├── tasks/                         # Benchmark problem definitions
│   ├── task_001_concurrent_lru_cache/
│   │   ├── problem.md             # Task specification
│   │   ├── baseline.py            # Buggy baseline implementation
│   │   ├── solution.py            # Gold-standard human solution
│   │   └── test_task.py           # Multi-threaded stress test suite
│   └── task_002_async_token_bucket_limiter/
│       ├── problem.md
│       ├── baseline.py
│       ├── solution.py
│       └── test_task.py
├── tests/                         # Harness self-test suite
├── Dockerfile                     # Multi-stage production container
└── requirements.txt               # Pinned dependencies
```

---

## ⚡ Quickstart

### 1. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/thangninjazz/ai-coding-benchmark-harness.git
cd ai-coding-benchmark-harness
pip install -r requirements.txt
```

### 2. List Available Benchmark Tasks
```bash
python -m harness.cli list
```

### 3. Verify Reference (Gold) Solutions
Run the benchmark across all tasks to verify that reference human solutions achieve 100% pass rates:
```bash
python -m harness.cli run-all --verify-gold
```

### 4. Run Harness Self-Tests
Verify the integrity of the evaluation harness itself:
```bash
pytest tests/ -v
```

---

## 🐳 Docker Execution

Build and run within a completely hermetic container:
```bash
docker build -t ai-benchmark-harness:latest .
docker run --rm -it ai-benchmark-harness:latest run-all --verify-gold
```

---

## 👤 Author
* **Do Van Thang** ([@thangninjazz](https://github.com/thangninjazz))  
  *Engineer in Computer Engineering, Hanoi University of Science and Technology (HUST)*
