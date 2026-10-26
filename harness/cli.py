"""
Command-line interface.

    python -m harness.cli list
    python -m harness.cli run TASK_ID [--candidate FILE | --verify-gold] [--backend docker]
    python -m harness.cli run-all [--verify-gold]
    python -m harness.cli verify [TASK_ID] [--repeat N]     # test the tests (gold/baseline/mutants)
    python -m harness.cli calibrate TASK_ID [--write]       # derive FAIL_TO_PASS / PASS_TO_PASS
    python -m harness.cli score TASK_ID SAMPLE.py ... -k 1 -k 5   # unbiased pass@k
    python -m harness.cli eval-model [TASK_ID] --provider anthropic --model M -n 5 -k 1 -k 5 --yes
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from harness.evaluator import TaskEvaluator, TaskLoadError
from harness.metrics import aggregate_pass_at_k, pass_at_k
from harness.models import BenchmarkResult, TaskDefinition, TestStatus
from harness.observability import ExitCode, MetricsCollector, setup_logging
from harness.sampling import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicGenerator,
    CommandGenerator,
    GenerationError,
    Generator,
    run_samples,
)
from harness.sandbox import make_sandbox
from harness.verify import calibrate, verify_task

ROOT = Path(__file__).resolve().parent.parent


def _out(text: str = "") -> None:
    """print() that survives consoles whose encoding cannot represent a character."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="AI Coding Benchmark Harness - SWE-bench style offline evaluation",
    )
    parser.add_argument("--tasks-dir", type=Path, default=ROOT / "tasks")
    parser.add_argument("--backend", choices=["local", "docker"], default="local",
                        help="execution backend (docker = network-less, read-only container)")
    parser.add_argument("--image", default="ai-benchmark-harness:latest", help="docker image")
    parser.add_argument("--timeout", type=int, default=None, help="override per-task timeout (s)")
    parser.add_argument("--log-json", action="store_true", help="output structured JSON logs to stderr")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="log level",
    )
    parser.add_argument("--metrics", action="store_true", help="output summary metrics after evaluation")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="list benchmark tasks")

    for name, help_ in (("run", "evaluate one task"), ("run-all", "evaluate every task")):
        p = sub.add_parser(name, help=help_)
        if name == "run":
            p.add_argument("task_id")
        group = p.add_mutually_exclusive_group()
        group.add_argument("--verify-gold", action="store_true", help="evaluate the reference solution")
        group.add_argument("--candidate", type=Path, help="evaluate this file as the implementation")
        p.add_argument("--json", type=Path, help="write machine-readable results to this file")
        p.add_argument("--metrics", action="store_true", help="output summary metrics after evaluation")

    p = sub.add_parser("verify", help="check a task's tests: gold passes, baseline fails, mutants die")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--repeat", type=int, default=1, help="repeat every run N times (flakiness check)")
    p.add_argument("--workers", type=int, default=4)

    p = sub.add_parser("calibrate", help="derive FAIL_TO_PASS / PASS_TO_PASS from baseline runs")
    p.add_argument("task_id")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--write", action="store_true", help="write the result into task.json")

    p = sub.add_parser("score", help="pass@k for N candidate samples of one task")
    p.add_argument("task_id")
    p.add_argument("samples", nargs="+", type=Path)
    p.add_argument("-k", type=int, action="append", help="k values (default: 1)")

    p = sub.add_parser("eval-model", help="sample a model on the tasks and report pass@k")
    p.add_argument("task_id", nargs="?", help="task to evaluate (default: all tasks)")
    p.add_argument("--provider", choices=["command", "anthropic"], required=True)
    p.add_argument("--cmd", help="command provider: shell command, prompt on stdin, answer on stdout")
    p.add_argument("--model", default=DEFAULT_ANTHROPIC_MODEL, help="anthropic provider: model id")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-tokens", type=int, default=32000)
    p.add_argument("-n", type=int, default=5, help="samples per task")
    p.add_argument("-k", type=int, action="append", help="k values (default: 1)")
    p.add_argument("--workers", type=int, default=2, help="parallel samples (generation + scoring)")
    p.add_argument("--save-dir", type=Path, help="keep prompts and raw replies here for audit")
    p.add_argument("--json", type=Path, help="write machine-readable results")
    p.add_argument("--yes", action="store_true", help="confirm that paid API calls may be made")
    return parser


def _select(tasks: List[TaskDefinition], task_id: Optional[str]) -> List[TaskDefinition]:
    if task_id is None:
        return tasks
    chosen = [t for t in tasks if t.task_id == task_id]
    if not chosen:
        _out(f"Error: task '{task_id}' not found. Available: {[t.task_id for t in tasks]}")
        sys.exit(ExitCode.USAGE_ERROR.value)
    return chosen


def _print_result(res: BenchmarkResult) -> None:
    mark = "PASS" if res.resolved else "FAIL"
    _out(
        f"  [{mark}] {res.task_id:<40} {res.status.value:<20} "
        f"{res.passed_tests}/{res.total_tests} tests, {res.duration_seconds:.2f}s"
    )
    if res.error_message:
        _out(f"         {res.error_message}")
    for v in res.anti_cheat_violations:
        _out(f"         anti-cheat: {v}")


def cmd_run(args, evaluator: TaskEvaluator, tasks: List[TaskDefinition]) -> int:
    tasks = _select(tasks, getattr(args, "task_id", None))
    candidate = args.candidate.read_text(encoding="utf-8") if args.candidate else None
    label = "gold solution" if args.verify_gold else ("candidate" if candidate is not None else "baseline")
    logger = logging.getLogger("harness")
    logger.info("Evaluating %s [%s backend] on %d task(s)", label, evaluator.sandbox.name, len(tasks))
    _out(f"\nEvaluating {label} [{evaluator.sandbox.name} backend]\n")
    results = []
    collector = MetricsCollector()
    for t in tasks:
        logger.debug("Starting task %s", t.task_id)
        res = evaluator.evaluate_task(t, candidate_code=candidate, use_gold=args.verify_gold)
        results.append(res)
        collector.record(res)
        logger.info(
            "Task %s finished with status %s in %.2fs",
            t.task_id,
            res.status.value,
            res.duration_seconds,
        )
        _print_result(res)

    resolved = sum(r.resolved for r in results)
    _out(f"\nResolved {resolved}/{len(results)} tasks.\n")
    if getattr(args, "metrics", False):
        summary = collector.summary()
        _out(f"Metrics Summary:\n{json.dumps(summary, indent=2)}\n")

    if args.json:
        args.json.write_text(
            json.dumps([r.model_dump(mode="json") for r in results], indent=2), encoding="utf-8"
        )

    if any(r.status == TestStatus.ANTI_CHEAT_VIOLATION for r in results):
        return ExitCode.TAMPERING_OR_VIOLATION.value
    if any(r.status == TestStatus.TIMEOUT for r in results):
        return ExitCode.TIMEOUT.value
    if any(r.status in (TestStatus.FAILED, TestStatus.PATCH_ERROR, TestStatus.ERROR) for r in results):
        return ExitCode.FAILED.value
    return ExitCode.RESOLVED.value


def cmd_verify(args, evaluator: TaskEvaluator, tasks: List[TaskDefinition]) -> int:
    ok = True
    for task in _select(tasks, args.task_id):
        _out(f"\n== {task.task_id} ==")
        report = verify_task(evaluator, task, repeat=args.repeat, workers=args.workers,
                             progress=lambda m: _out(f"   ... {m}"))
        for check in report.checks:
            suffix = f"  -- {check.detail}" if check.detail else ""
            _out(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}{suffix}")
        _out(f"  mutation score: {report.mutants_killed}/{report.mutants_total} "
             f"({report.mutation_score:.0%}); verdict: {'VALID' if report.ok else 'INVALID'}")
        ok &= report.ok
    return 0 if ok else 1


def cmd_calibrate(args, evaluator: TaskEvaluator, tasks: List[TaskDefinition]) -> int:
    task = _select(tasks, args.task_id)[0]
    cal = calibrate(evaluator, task, repeat=args.repeat)
    _out(f"FAIL_TO_PASS ({len(cal.fail_to_pass)}): {cal.fail_to_pass}")
    _out(f"PASS_TO_PASS ({len(cal.pass_to_pass)}): {cal.pass_to_pass}")
    if cal.flaky:
        _out(f"FLAKY on baseline (fix the tests before shipping): {cal.flaky}")
        return 1
    if args.write:
        path = task.path / "task.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["fail_to_pass"], data["pass_to_pass"] = cal.fail_to_pass, cal.pass_to_pass
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _out(f"updated {path}")
    return 0


def cmd_score(args, evaluator: TaskEvaluator, tasks: List[TaskDefinition]) -> int:
    task = _select(tasks, args.task_id)[0]
    ks = sorted(set(args.k or [1]))
    outcomes = []
    for sample in args.samples:
        res = evaluator.evaluate_task(task, candidate_code=sample.read_text(encoding="utf-8"))
        outcomes.append(res.resolved)
        _out(f"  {sample.name:<30} {res.status.value}")
    n, c = len(outcomes), sum(outcomes)
    _out(f"\n{task.task_id}: n={n} correct={c}")
    for k in ks:
        if k > n:
            _out(f"  pass@{k}: n/a (need at least {k} samples)")
        else:
            _out(f"  pass@{k}: {pass_at_k(n, c, k):.4f}")
    return 0


def cmd_eval_model(args, evaluator: TaskEvaluator, tasks: List[TaskDefinition]) -> int:
    chosen = _select(tasks, args.task_id)
    ks = sorted(set(args.k or [1]))
    if args.n < 1 or max(ks) > args.n:
        _out(f"Error: every k must satisfy 1 <= k <= n (n={args.n}, k={ks})")
        return 2
    calls = args.n * len(chosen)

    if args.provider == "anthropic" and not args.yes:
        _out(
            f"This would make {calls} paid API calls ({args.n} samples x {len(chosen)} task(s)) to "
            f"{args.model}.\nNothing was sent. Re-run with --yes to proceed."
        )
        return 2
    generator: Generator
    try:
        if args.provider == "command":
            if not args.cmd:
                _out("Error: --provider command needs --cmd")
                return 2
            generator = CommandGenerator(args.cmd)
        else:
            generator = AnthropicGenerator(args.model, args.effort, args.max_tokens)
    except GenerationError as e:
        _out(f"Error: {e}")
        return 2

    _out(f"\nSampling {generator.name}: {args.n} sample(s) x {len(chosen)} task(s)\n")
    per_task = []
    for task in chosen:
        ts = run_samples(evaluator, task, generator, args.n, workers=args.workers, save_dir=args.save_dir)
        per_task.append(ts)
        scores = "  ".join(
            f"pass@{k}={'n/a' if ts.pass_at(k) is None else format(ts.pass_at(k), '.3f')}" for k in ks
        )
        extra = f"  ({ts.generation_errors} request error(s) excluded)" if ts.generation_errors else ""
        _out(f"  {task.task_id:<40} n={ts.n} resolved={ts.correct}  {scores}{extra}")
        for sample in ts.samples:
            if sample.error and not sample.resolved:
                _out(f"      sample {sample.index}: {sample.status}: {str(sample.error)[:140]}")

    usable = {ts.task_id: ts.outcomes() for ts in per_task if ts.n >= max(ks)}
    overall = aggregate_pass_at_k(usable, ks) if usable else {}
    if len(per_task) > 1 and overall:
        _out("\n  mean over tasks: " + "  ".join(f"pass@{k}={overall[k]:.3f}" for k in ks))
    tokens_in = sum(s.input_tokens for ts in per_task for s in ts.samples)
    tokens_out = sum(s.output_tokens for ts in per_task for s in ts.samples)
    if tokens_in or tokens_out:
        _out(f"  tokens: {tokens_in:,} in / {tokens_out:,} out")
    _out()
    if args.json:
        payload = {
            "generator": generator.name,
            "n": args.n,
            "ks": ks,
            "overall_pass_at_k": {str(k): v for k, v in overall.items()},
            "tasks": [ts.to_dict(ks) for ts in per_task],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0

    setup_logging(json_output=args.log_json, level=args.log_level)

    try:
        evaluator = TaskEvaluator(
            args.tasks_dir, timeout_seconds=args.timeout, sandbox=make_sandbox(args.backend, args.image)
        )
        tasks = evaluator.discover_tasks()
    except TaskLoadError as e:
        _out(f"Error: {e}")
        return ExitCode.USAGE_ERROR.value

    if args.command == "list":
        _out(f"\nDiscovered {len(tasks)} benchmark tasks:")
        for t in tasks:
            _out(f"  {t.task_id:<40} [{t.difficulty}] {t.title}")
        _out()
        return 0
    handlers = {
        "run": cmd_run, "run-all": cmd_run, "verify": cmd_verify,
        "calibrate": cmd_calibrate, "score": cmd_score, "eval-model": cmd_eval_model,
    }
    return handlers[args.command](args, evaluator, tasks)


if __name__ == "__main__":
    sys.exit(main())
