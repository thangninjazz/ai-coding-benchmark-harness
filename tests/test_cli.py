import json

import pytest

from harness.cli import main

from .conftest import TASK1


def test_list(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Discovered 2 benchmark tasks" in out and TASK1 in out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_run_all_gold_resolves_everything_and_writes_json(capsys, tmp_path):
    report = tmp_path / "r.json"
    assert main(["run-all", "--verify-gold", "--json", str(report)]) == 0
    assert "Resolved 2/2" in capsys.readouterr().out
    data = json.loads(report.read_text())
    assert [r["resolved"] for r in data] == [True, True]


def test_run_baseline_exits_nonzero(capsys):
    assert main(["run", TASK1]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_unknown_task_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["run", "task_nope"])
    assert exc.value.code == 2


def test_candidate_file_is_evaluated(tasks, tmp_path, capsys):
    candidate = tmp_path / "cand.py"
    candidate.write_text(tasks[TASK1].solution_file.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["run", TASK1, "--candidate", str(candidate)]) == 0


def test_score_reports_unbiased_pass_at_k(tasks, tmp_path, capsys):
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text(tasks[TASK1].solution_file.read_text(encoding="utf-8"), encoding="utf-8")
    bad.write_text(tasks[TASK1].baseline_file.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["score", TASK1, str(good), str(bad), "-k", "1", "-k", "2", "-k", "5"]) == 0
    out = capsys.readouterr().out
    assert "n=2 correct=1" in out
    assert "pass@1: 0.5000" in out and "pass@2: 1.0000" in out and "pass@5: n/a" in out


def test_non_ascii_output_does_not_crash_on_legacy_consoles(monkeypatch, capsys):
    import builtins

    from harness import cli

    real_print = builtins.print
    calls = []

    def picky_print(text="", *a, **k):
        calls.append(text)
        if any(ord(c) > 127 for c in str(text)) and len(calls) == 1:
            raise UnicodeEncodeError("cp932", "x", 0, 1, "unsupported")
        real_print(text, *a, **k)

    monkeypatch.setattr(builtins, "print", picky_print)
    cli._out("✓ done")
    assert "? done" in capsys.readouterr().out
