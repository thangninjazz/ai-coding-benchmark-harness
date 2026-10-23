"""
Sampling candidates from a model and measuring pass@k.

The harness is provider-neutral: a *generator* is anything that turns a prompt into text.

* ``CommandGenerator``   - runs a shell command, prompt on stdin, answer on stdout. Works
  with any model or CLI you can script.
* ``AnthropicGenerator`` - calls the Anthropic Messages API (optional dependency, see
  ``pip install anthropic``).

Samples are drawn independently, extracted from the model's reply, scored by the same
evaluator as everything else, and fed to the unbiased pass@k estimator. Requests that fail
for infrastructure reasons are *excluded* from ``n`` (and counted separately) rather than
silently scored as wrong; a reply with no usable code *is* the model's failure and counts
as unresolved.
"""

import ast
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Protocol, Sequence

from harness.evaluator import TaskEvaluator
from harness.metrics import pass_at_k
from harness.models import TaskDefinition

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
Effort = Literal["low", "medium", "high", "xhigh", "max"]
SYSTEM_PROMPT = (
    "You are an expert software engineer. Follow the task's output format exactly: reply with "
    "the requested code block and nothing that has to be parsed outside it."
)
_FENCE = re.compile(r"```([A-Za-z0-9_+.-]*)[ \t]*\n(.*?)```", re.DOTALL)


class GenerationError(Exception):
    """A request could not be completed (network, quota, refusal, timeout ...)."""


@dataclass
class Generation:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class Generator(Protocol):
    name: str

    def generate(self, prompt: str) -> Generation: ...


# ------------------------------------------------------------------------------ prompts
def build_prompt(task: TaskDefinition) -> str:
    """The prompt a model sees. Hidden tests, baselines and gold solutions are never included."""
    meta = task.meta
    allowed = ", ".join(f"`{m}`" for m in meta.allowed_imports if m != "__future__") or "none"
    if meta.kind == "single_file":
        return (
            "Implement the following task.\n\n"
            f"## Task\n\n{task.problem_statement.strip()}\n\n"
            "## Output format\n\n"
            "Reply with the complete contents of `implementation.py` in ONE fenced ```python block. "
            f"Use only the Python standard library; the imports you may use are: {allowed}. "
            "Do not include tests or example usage.\n"
        )
    files = []
    repo = task.path / "repo"
    for path in sorted(repo.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(repo).as_posix()
        files.append(f"### `{rel}`\n\n```python\n{path.read_text(encoding='utf-8').rstrip()}\n```\n")
    editable = ", ".join(f"`{p}`" for p in meta.editable_paths)
    return (
        "Fix the issue described below by writing a patch for the repository.\n\n"
        f"## Task\n\n{task.problem_statement.strip()}\n\n"
        "## Repository\n\n" + "\n".join(files) + "\n"
        "## Output format\n\n"
        "Reply with ONE fenced ```diff block containing a unified diff (as `git diff` prints it, with "
        "`a/` and `b/` path prefixes relative to the repository root). You may add or modify `.py` "
        f"files only under {editable}. Context lines must match the current files exactly. Do not "
        f"touch tests. The imports you may use are: {allowed}.\n"
    )


def extract_candidate(task: TaskDefinition, text: str) -> Optional[str]:
    """Pull the code (or diff) out of a model reply; ``None`` if there is none."""
    wanted = {"python", "py", "python3", ""} if task.meta.kind == "single_file" else {"diff", "patch", ""}
    blocks = [(lang.lower(), body) for lang, body in _FENCE.findall(text) if body.strip()]
    preferred = [body for lang, body in blocks if lang in wanted]
    if preferred:
        body = preferred[-1]
        return body if body.endswith("\n") else body + "\n"
    if blocks:
        return None  # fenced, but in an unrelated language
    stripped = text.strip()
    if task.meta.kind == "patch":
        return text if stripped.startswith(("--- ", "diff --git")) else None
    try:
        ast.parse(stripped)
    except (SyntaxError, ValueError):
        return None
    return stripped + "\n" if stripped else None


# -------------------------------------------------------------------------- generators
class CommandGenerator:
    """Run a shell command: prompt on stdin, completion on stdout."""

    def __init__(self, command: str, timeout: int = 900):
        self.command = command
        self.timeout = timeout
        self.name = f"command[{command[:40]}]"

    def generate(self, prompt: str) -> Generation:
        try:
            proc = subprocess.run(
                self.command, input=prompt, capture_output=True, text=True, encoding="utf-8",
                errors="replace", shell=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise GenerationError(f"command timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise GenerationError(f"command exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
        return Generation(proc.stdout)


class AnthropicGenerator:
    """Anthropic Messages API, streamed, with adaptive thinking and an explicit effort level.

    Deliberately *no* model fallback: if a safety classifier declines a request, silently
    re-running it on another model would make the result measure a different model than the
    one you named. A refusal is reported as a generation error instead.
    Sampling parameters (``temperature`` and friends) are not sent: current models reject them.
    """

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        effort: Effort = "high",
        max_tokens: int = 32000,
        client: Optional[Any] = None,
    ):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on the environment
                raise GenerationError(
                    "the 'anthropic' package is not installed: pip install anthropic"
                ) from e
            client = anthropic.Anthropic()
        self.client = client
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self.name = f"anthropic[{model}, effort={effort}]"

    def generate(self, prompt: str) -> Generation:
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                final = stream.get_final_message()
        except Exception as e:  # SDK errors are already retried; surface what is left
            raise GenerationError(f"{type(e).__name__}: {str(e)[:300]}") from e
        if final.stop_reason == "refusal":
            category = getattr(getattr(final, "stop_details", None), "category", None)
            raise GenerationError(f"model refused (category={category})")
        text = "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", "") == "text")
        usage = getattr(final, "usage", None)
        return Generation(
            text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


# ------------------------------------------------------------------------------ running
@dataclass
class Sample:
    index: int
    status: str  # an evaluator status, "NO_CODE", or "GENERATION_ERROR"
    resolved: bool = False
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TaskSamples:
    task_id: str
    samples: List[Sample] = field(default_factory=list)

    @property
    def usable(self) -> List[Sample]:
        return [s for s in self.samples if s.status != "GENERATION_ERROR"]

    @property
    def n(self) -> int:
        return len(self.usable)

    @property
    def correct(self) -> int:
        return sum(s.resolved for s in self.usable)

    @property
    def generation_errors(self) -> int:
        return len(self.samples) - self.n

    def pass_at(self, k: int) -> Optional[float]:
        return pass_at_k(self.n, self.correct, k) if 1 <= k <= self.n else None

    def outcomes(self) -> List[bool]:
        return [s.resolved for s in self.usable]

    def to_dict(self, ks: Sequence[int]) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "n": self.n,
            "correct": self.correct,
            "generation_errors": self.generation_errors,
            "pass_at_k": {str(k): self.pass_at(k) for k in ks},
            "input_tokens": sum(s.input_tokens for s in self.samples),
            "output_tokens": sum(s.output_tokens for s in self.samples),
            "samples": [s.__dict__ for s in self.samples],
        }


def run_samples(
    evaluator: TaskEvaluator,
    task: TaskDefinition,
    generator: Generator,
    n: int,
    workers: int = 2,
    save_dir: Optional[Path] = None,
) -> TaskSamples:
    """Draw ``n`` independent samples for ``task`` and score each one."""
    prompt = build_prompt(task)
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / f"{task.task_id}.prompt.txt").write_text(prompt, encoding="utf-8")

    def one(index: int) -> Sample:
        try:
            generated = generator.generate(prompt)
        except GenerationError as e:
            return Sample(index, "GENERATION_ERROR", error=str(e))
        tokens_in, tokens_out = generated.input_tokens, generated.output_tokens
        if save_dir:
            (save_dir / f"{task.task_id}.sample{index}.txt").write_text(generated.text, encoding="utf-8")
        candidate = extract_candidate(task, generated.text)
        if candidate is None:
            return Sample(
                index, "NO_CODE", error="no usable code block in the reply",
                input_tokens=tokens_in, output_tokens=tokens_out,
            )
        result = evaluator.evaluate_code(task, candidate)
        return Sample(
            index, result.status.value, resolved=result.resolved, error=result.error_message,
            input_tokens=tokens_in, output_tokens=tokens_out,
        )

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        samples = list(pool.map(one, range(n)))
    return TaskSamples(task.task_id, samples)
