"""Red-team tests: forged JUnit XML reports.

A candidate attempts to forge a JUnit XML report declaring that all expected tests passed
(and the canary test failed), exiting with code 1 to spoof a legitimate successful run.
Under the vulnerable architecture, these succeed when the static AST scan is bypassed.
They are marked xfail here and flipped to passing by Step 2.
"""

from harness.models import TestStatus

from .conftest import make_patch_cheat


def _build_forged_xml(task) -> str:
    expected = task.meta.fail_to_pass + task.meta.pass_to_pass
    cases = "\n".join(
        f'<testcase classname="test_task" name="{t}" time="0.001" />' for t in expected
    )
    canary = (
        '<testcase classname="_harness_canary" name="test_canary_must_fail" time="0.001">'
        '<failure message="failed">intentional</failure></testcase>'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites>\n'
        f'  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="{len(expected) + 1}">\n'
        f"    {canary}\n"
        f"    {cases}\n"
        "  </testsuite>\n"
        "</testsuites>"
    )


# All tests below originally marked xfail; now passing under hardened verdict architecture.

def test_forged_junit_argv_single_file(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    xml = _build_forged_xml(task)
    cheat_code = f"""
import sys, os
path = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--junitxml=")][0]
with open(path, "w", encoding="utf-8") as f:
    f.write({repr(xml)})
os._exit(1)
"""
    res = local_evaluator.evaluate_code(task, cheat_code)
    assert not res.resolved
    assert res.status != TestStatus.RESOLVED


def test_forged_junit_argv_patch_task(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["patch"]
    xml = _build_forged_xml(task)
    injected = f"""
import sys, os
path = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--junitxml=")][0]
with open(path, "w", encoding="utf-8") as f:
    f.write({repr(xml)})
os._exit(1)
"""
    patch = make_patch_cheat(task, injected)
    res = local_evaluator.evaluate_code(task, patch)
    assert not res.resolved
    assert res.status != TestStatus.RESOLVED


def test_forged_junit_guessed_path_single_file(local_evaluator, target_tasks, no_static_scan):
    task = target_tasks["single"]
    xml = _build_forged_xml(task)
    cheat_code = f"""
import glob, os
from pathlib import Path
out_dirs = glob.glob(str(Path("..") / "bench-out-*"))
for d in out_dirs:
    with open(os.path.join(d, "junit.xml"), "w", encoding="utf-8") as f:
        f.write({repr(xml)})
os._exit(1)
"""
    res = local_evaluator.evaluate_code(task, cheat_code)
    assert not res.resolved
    assert res.status != TestStatus.RESOLVED
