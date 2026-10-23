import pytest

from harness.anti_cheat import scan_code, verify_code_integrity

CLEAN = [
    "def calculate(a, b):\n    return a + b\n",
    (
        "import threading\nfrom collections import OrderedDict\n\n"
        "class C:\n    def __init__(self):\n        self.lock = threading.RLock()\n"
    ),
    "mock = 1\nmodules = {}\nopen_count = 0\n",  # names that merely resemble forbidden ones
    "from __future__ import annotations\nimport heapq\n\n_MISSING = object()\n",
    (
        '"""docstring"""\nclass A:\n    """doc"""\n    x: int = 1\n'
        "    def __eq__(self, other):\n        return self.x == other.x\n"
    ),
    "x = getattr(object(), 'real_attr', None)\n",
]

CHEATS = {
    "sys.modules": "import sys\nsys.modules['x'] = None",
    "from sys import modules": "from sys import modules\nmodules['x'] = None",
    "alias import": "import sys as s\ns.modules['x'] = 1",
    "alias assignment": "import sys\nm = sys\nm.modules['x'] = 1",
    "__import__": "m = __import__('sys')",
    "getattr modules": "import sys\ngetattr(sys, 'modules')",
    "getattr dynamic": "import sys\nx = 'mod' + 'ules'\ngetattr(sys, x)",
    "import pytest": "import pytest",
    "import pytest as p": "import pytest as p",
    "from pytest import raises": "from pytest import raises",
    "from unittest import mock": "from unittest import mock",
    "import unittest.mock": "import unittest.mock",
    "importlib": "import importlib\nimportlib.import_module('unittest.mock')",
    "builtins": "import builtins\nbuiltins.assert_ = 1",
    "__builtins__ name": "x = __builtins__",
    "open()": "open('test_task.py').read()",
    "write conftest": "open('conftest.py', 'w').write('x')",
    "os._exit": "import os\nos._exit(0)",
    "from os import _exit": "from os import _exit\n_exit(0)",
    "sys.exit": "import sys\nsys.exit(0)",
    "exit()": "exit(0)",
    "exec": "exec('x = 1')",
    "eval": "eval('1')",
    "compile": "compile('1', 'f', 'eval')",
    "setattr": "setattr(object, 'x', 1)",
    "subprocess": "import subprocess\nsubprocess.run(['ls'])",
    "socket": "import socket",
    "ctypes": "import ctypes",
    "inspect": "import inspect\ninspect.stack()",
    "frame hack": "import sys\nf = sys._getframe(1)",
    "frame attr": "def f(fr):\n    return fr.f_back.f_globals",
    "subclasses walk": "x = ().__class__.__base__.__subclasses__()",
    "func globals": "def f():\n    pass\nf.__globals__['x'] = 1",
    "atexit": "import atexit",
    "os.kill": "import os\nos.kill(1, 9)",
    "eq always true": "class A:\n    def __eq__(self, other):\n        return True\n",
    "bool always true": "class A:\n    def __bool__(self):\n        return True\n",
    "getattr interception": "class A:\n    def __getattr__(self, n):\n        return 1\n",
    "import-time call": "print('hi')\n",
    "import-time loop": "for i in range(3):\n    pass\n",
    "import-time assert": "assert True\n",
    "class-body side effect": "class A:\n    print('x')\n",
    "relative import": "from . import x",
}


@pytest.mark.parametrize("code", CLEAN)
def test_legitimate_code_is_not_flagged(code):
    assert verify_code_integrity(code) == []


@pytest.mark.parametrize("name", sorted(CHEATS))
def test_cheat_is_detected(name):
    assert verify_code_integrity(CHEATS[name]), f"missed: {name}"


def test_violations_carry_rule_ids_and_line_numbers():
    found = scan_code("import sys\n\nx = sys.modules\n")
    assert [(v.rule, v.lineno) for v in found] == [("AC003", 3)]
    assert "line 3" in str(found[0]) and "AC003" in str(found[0])


def test_syntax_error_is_reported_not_raised():
    (v,) = scan_code("def broken(:\n")
    assert v.rule == "AC000"


def test_null_bytes_and_huge_input_are_rejected_cleanly():
    assert scan_code("x = 1\x00")[0].rule == "AC000"
    assert scan_code("x = 1\n" * 100_000)[0].rule == "AC000"


def test_deeply_nested_source_does_not_crash():
    nested = "x = " + "[" * 500 + "]" * 500
    assert isinstance(scan_code(nested), list)


def test_allowlist_rejects_unlisted_imports():
    found = scan_code("import json\nimport threading\n", allowed_imports=["threading"])
    assert [v.rule for v in found] == ["AC002"]
    assert "json" in found[0].message


def test_allowlist_matches_submodules():
    assert scan_code("from collections.abc import Mapping", allowed_imports=["collections"]) == []


def test_reference_material_is_clean_under_each_tasks_allowlist(tasks):
    for task in tasks.values():
        for path in (task.solution_file, task.baseline_file):
            assert scan_code(path.read_text(encoding="utf-8"), task.meta.allowed_imports) == [], path
