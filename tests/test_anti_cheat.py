from harness.anti_cheat import verify_code_integrity


def test_clean_code_passes():
    code = """
def calculate(a, b):
    return a + b
"""
    assert verify_code_integrity(code) == []


def test_forbidden_mock_import_detected():
    code = """
import unittest.mock
def solve():
    return unittest.mock.MagicMock()
"""
    violations = verify_code_integrity(code)
    assert any("Forbidden mock library" in v for v in violations)


def test_sys_modules_tampering_detected():
    code = """
import sys
sys.modules['some_module'] = None
"""
    violations = verify_code_integrity(code)
    assert any("sys.modules" in v for v in violations)
