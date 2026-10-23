"""Sampling, extraction and pass@k plumbing. No real model is ever called here."""

import json
import sys
from types import SimpleNamespace

import pytest

from harness.cli import main
from harness.evaluator import TaskEvaluator
from harness.sampling import (
    AnthropicGenerator,
    CommandGenerator,
    Generation,
    GenerationError,
    build_prompt,
    extract_candidate,
    run_samples,
)

from .conftest import TASK1, TASK3
from .test_verify import make_task

GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"


def fenced(code, lang="python"):
    return f"Here you go.\n\n```{lang}\n{code}```\n\nHope that helps."


@pytest.fixture
def mini(tmp_path):
    make_task(tmp_path)
    evaluator = TaskEvaluator(tmp_path)
    return tmp_path, evaluator, evaluator.discover_tasks()[0]


class Scripted:
    """Deterministic fake model: replays a list of replies."""

    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def generate(self, prompt):
        reply = self.replies[self.calls % len(self.replies)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return Generation(reply, input_tokens=10, output_tokens=5)


# ------------------------------------------------------------------------- extraction
def test_extracts_the_last_python_block(mini):
    _, _, task = mini
    reply = fenced("x = 1\n") + "\nActually, better:\n" + fenced(GOOD)
    assert extract_candidate(task, reply) == GOOD


def test_extracts_unlabelled_blocks_and_ignores_other_languages(mini):
    _, _, task = mini
    assert extract_candidate(task, "```\n" + GOOD + "```") == GOOD
    assert extract_candidate(task, fenced("echo hi\n", "bash")) is None


def test_bare_code_without_fences_is_accepted_only_if_it_parses(mini):
    _, _, task = mini
    assert extract_candidate(task, GOOD) == GOOD
    assert extract_candidate(task, "I think you should use addition.") is None
    assert extract_candidate(task, "") is None


def test_patch_tasks_want_a_diff_block(tasks):
    cart = tasks[TASK3]
    diff = "--- a/shop/money.py\n+++ b/shop/money.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    assert extract_candidate(cart, fenced(diff, "diff")) == diff
    assert extract_candidate(cart, fenced(diff, "patch")) == diff
    assert extract_candidate(cart, diff) == diff  # bare diff accepted
    assert extract_candidate(cart, fenced("print(1)\n", "python")) is None
    assert extract_candidate(cart, "no code here") is None


# ---------------------------------------------------------------------------- prompts
def test_prompt_never_leaks_tests_baseline_or_gold(tasks):
    for task in tasks.values():
        prompt = build_prompt(task)
        assert task.problem_statement.strip() in prompt
        assert "def test_" not in prompt
        assert "FAIL_TO_PASS" not in prompt and "mutant" not in prompt.lower()
        for name in task.meta.allowed_imports:
            if name != "__future__":
                assert f"`{name}`" in prompt


def test_patch_prompt_contains_the_repository_files(tasks):
    prompt = build_prompt(tasks[TASK3])
    assert "### `shop/cart.py`" in prompt and "def total(self" in prompt
    assert "```diff" in prompt and "`shop/`" in prompt


def test_single_file_prompt_asks_for_implementation_py(tasks):
    assert "implementation.py" in build_prompt(tasks[TASK1])


# ------------------------------------------------------------------------- run_samples
def test_pass_at_k_from_scripted_samples(mini, tmp_path):
    _, evaluator, task = mini
    model = Scripted([fenced(GOOD), fenced(BAD), fenced(GOOD), "no code, sorry"])
    out = run_samples(evaluator, task, model, n=4, workers=1, save_dir=tmp_path / "raw")
    assert (out.n, out.correct, out.generation_errors) == (4, 2, 0)
    assert [s.status for s in out.samples] == ["RESOLVED", "FAILED", "RESOLVED", "NO_CODE"]
    assert out.pass_at(1) == pytest.approx(0.5)
    assert out.pass_at(4) == 1.0 and out.pass_at(5) is None
    assert (tmp_path / "raw" / f"{task.task_id}.prompt.txt").is_file()
    assert (tmp_path / "raw" / f"{task.task_id}.sample1.txt").read_text() == fenced(BAD)
    assert sum(s.output_tokens for s in out.samples) == 20  # even the NO_CODE reply cost tokens


def test_request_errors_are_excluded_from_n_not_scored_as_wrong(mini):
    _, evaluator, task = mini
    model = Scripted([fenced(GOOD), GenerationError("503"), fenced(GOOD)])
    out = run_samples(evaluator, task, model, n=3, workers=1)
    assert (out.n, out.correct, out.generation_errors) == (2, 2, 1)
    assert out.pass_at(1) == 1.0
    assert out.samples[1].status == "GENERATION_ERROR"


def test_a_cheating_reply_is_scored_by_the_normal_pipeline(mini):
    _, evaluator, task = mini
    cheat = fenced("import os\nos._exit(0)\n")
    out = run_samples(evaluator, task, Scripted([cheat]), n=1, workers=1)
    assert out.correct == 0 and out.samples[0].status == "ANTI_CHEAT_VIOLATION"


# ----------------------------------------------------------------------- generators
def test_command_generator_round_trip(tmp_path):
    script = tmp_path / "echo_model.py"
    script.write_text("import sys\nprompt = sys.stdin.read()\nprint('LEN', len(prompt))\n")
    gen = CommandGenerator(f'"{sys.executable}" "{script}"')
    assert gen.generate("hello").text.strip() == "LEN 5"


def test_command_generator_reports_failures(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
    with pytest.raises(GenerationError, match="exited 3.*boom"):
        CommandGenerator(f'"{sys.executable}" "{bad}"').generate("x")
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    with pytest.raises(GenerationError, match="timed out"):
        CommandGenerator(f'"{sys.executable}" "{slow}"', timeout=1).generate("x")


class FakeStream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.final


class FakeClient:
    def __init__(self, final=None, error=None):
        self.final, self.error, self.kwargs = final, error, None
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return FakeStream(self.final)


def message(blocks, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, stop_details=stop_details,
        usage=SimpleNamespace(input_tokens=100, output_tokens=40),
    )


def test_anthropic_generator_request_shape_and_parsing():
    client = FakeClient(message([
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="part one "),
        SimpleNamespace(type="text", text="part two"),
    ]))
    gen = AnthropicGenerator("claude-opus-5-5", effort="xhigh", max_tokens=1234, client=client)
    out = gen.generate("PROMPT")
    assert out.text == "part one part two" and (out.input_tokens, out.output_tokens) == (100, 40)

    sent = client.kwargs
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 1234
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "xhigh"}
    assert sent["messages"] == [{"role": "user", "content": "PROMPT"}]
    # current models reject sampling parameters, forced tool use and prefills; none may be sent
    assert not {"temperature", "top_p", "top_k", "tools", "tool_choice", "fallbacks"} & set(sent)
    assert sent["messages"][-1]["role"] == "user"


def test_anthropic_generator_refusal_is_an_error_not_a_fallback():
    refusal = message([], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(GenerationError, match="refused.*cyber"):
        AnthropicGenerator(client=FakeClient(refusal)).generate("p")


def test_anthropic_generator_wraps_sdk_errors():
    gen = AnthropicGenerator(client=FakeClient(error=RuntimeError("rate limited")))
    with pytest.raises(GenerationError, match="RuntimeError: rate limited"):
        gen.generate("p")


# ---------------------------------------------------------------------------------- CLI
def test_cli_eval_model_with_a_command_provider(mini, tmp_path, capsys):
    root, _, _ = mini
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "good.txt").write_text(fenced(GOOD))
    (answers / "bad.txt").write_text(fenced(BAD))
    script = tmp_path / "model.py"
    script.write_text(
        "import sys, pathlib\n"
        "state = pathlib.Path(sys.argv[1]) / 'counter'\n"
        "i = int(state.read_text()) if state.exists() else 0\n"
        "state.write_text(str(i + 1))\n"
        "sys.stdin.read()\n"
        "print(pathlib.Path(sys.argv[1], 'good.txt' if i % 2 == 0 else 'bad.txt').read_text())\n"
    )
    report = tmp_path / "out.json"
    code = main([
        "--tasks-dir", str(root), "eval-model", "task_900_add", "--provider", "command",
        "--cmd", f'"{sys.executable}" "{script}" "{answers}"', "-n", "4", "-k", "1", "-k", "2",
        "--workers", "1", "--json", str(report),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "n=4 resolved=2" in out and "pass@1=0.500" in out and "pass@2=0.833" in out
    data = json.loads(report.read_text())
    assert data["tasks"][0]["correct"] == 2 and data["overall_pass_at_k"]["1"] == 0.5


def test_cli_refuses_paid_calls_without_confirmation(mini, capsys, monkeypatch):
    root, _, _ = mini
    monkeypatch.setattr(
        "harness.cli.AnthropicGenerator",
        lambda *a, **k: pytest.fail("must not construct a client before confirmation"),
    )
    code = main(["--tasks-dir", str(root), "eval-model", "--provider", "anthropic", "-n", "3"])
    assert code == 2
    out = capsys.readouterr().out
    assert "3 paid API calls" in out and "--yes" in out and "Nothing was sent" in out


@pytest.mark.parametrize("argv,message", [
    (["--provider", "command"], "needs --cmd"),
    (["--provider", "command", "--cmd", "x", "-n", "2", "-k", "3"], "1 <= k <= n"),
])
def test_cli_eval_model_argument_errors(mini, capsys, argv, message):
    root, _, _ = mini
    assert main(["--tasks-dir", str(root), "eval-model", *argv]) == 2
    assert message in capsys.readouterr().out
