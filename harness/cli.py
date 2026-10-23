"""
CLI Interface for AI Coding Benchmark Harness
Provides rich terminal execution, reporting, and task verification.
"""

import argparse
import sys
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from harness.evaluator import TaskEvaluator
from harness.models import TestStatus


def main():
    parser = argparse.ArgumentParser(
        description="AI Coding Benchmark Harness - SWE-bench style offline evaluation"
    )
    subparsers = parser.add_subparsers(dest="command")

    # list command
    subparsers.add_parser("list", help="List all available benchmark tasks")

    # run-all command
    run_all_parser = subparsers.add_parser("run-all", help="Run benchmark evaluation across all tasks")
    run_all_parser.add_argument("--verify-gold", action="store_true", help="Verify against reference human solutions")
    run_all_parser.add_argument("--timeout", type=int, default=20, help="Per-task execution timeout in seconds")

    # run task command
    run_parser = subparsers.add_parser("run", help="Run a specific benchmark task")
    run_parser.add_argument("task_id", help="ID of task to evaluate")
    run_parser.add_argument("--verify-gold", action="store_true", help="Test reference solution")

    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent.parent
    tasks_dir = root_dir / "tasks"
    evaluator = TaskEvaluator(tasks_dir=tasks_dir, timeout_seconds=getattr(args, "timeout", 20))

    if args.command == "list":
        tasks = evaluator.discover_tasks()
        print(f"\nDiscovered {len(tasks)} benchmark tasks:")
        for t in tasks:
            print(f"  • {t.task_id:<32} [{t.difficulty}] - {t.title}")
        print()

    elif args.command in ("run-all", "run"):
        tasks = evaluator.discover_tasks()
        if args.command == "run":
            tasks = [t for t in tasks if t.task_id == args.task_id]
            if not tasks:
                print(f"Error: Task '{args.task_id}' not found.")
                sys.exit(1)

        print(f"\nRunning benchmark evaluation ({'Gold Solution' if args.verify_gold else 'Baseline'})...\n")
        all_passed = True
        results = []

        for task in tasks:
            res = evaluator.evaluate_task(task, use_gold=args.verify_gold)
            results.append(res)
            status_symbol = "✓" if res.status == TestStatus.PASSED else "✗"
            print(f"  [{status_symbol}] {res.task_id:<32} {res.status.value:<10} ({res.passed_tests}/{res.total_tests} passed in {res.duration_seconds:.2f}s)")
            if res.status != TestStatus.PASSED:
                all_passed = False
                if res.error_message:
                    print(f"      Reason: {res.error_message}")

        print(f"\nCompleted {len(results)} tasks. All Passed: {all_passed}\n")
        sys.exit(0 if all_passed else 1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
