"""
Anti-Cheat & Shortcut Detection Module
Inspects proposed candidate diffs/code using Abstract Syntax Tree (AST) analysis
to catch lazy shortcuts, test-suite monkeypatching, and hardcoded assertion bypasses.
"""

import ast
from typing import List


class ShortcutDetectionVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if alias.name in ("unittest.mock", "mock", "pytest"):
                self.violations.append(
                    f"Forbidden mock library imported in implementation: {alias.name}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module in ("unittest.mock", "mock", "pytest"):
            self.violations.append(
                f"Forbidden mock submodule imported: {node.module}"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Detect sys.modules hijacking or dynamic function monkeypatching
        if isinstance(node.value, ast.Name):
            if node.value.id == "sys" and node.attr == "modules":
                self.violations.append("Unauthorized dynamic module manipulation via sys.modules")
        self.generic_visit(node)


def verify_code_integrity(code_content: str) -> List[str]:
    """
    Parses Python code and checks for anti-patterns and bypass attempts.
    Returns a list of violation messages.
    """
    try:
        tree = ast.parse(code_content)
    except SyntaxError as e:
        return [f"Syntax error in code submission: {e}"]

    visitor = ShortcutDetectionVisitor()
    visitor.visit(tree)
    return visitor.violations
