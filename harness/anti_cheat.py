"""
Anti-cheat static analysis.

Walks the AST of a candidate implementation and reports constructs that have no
legitimate use in a pure algorithmic solution but are the usual tools for gaming a
test run: patching the import system, reaching into the test runner, terminating the
interpreter early, executing dynamically built code, or defining objects that compare
equal to everything.

Design notes
------------
* Names are resolved through an alias table, so ``import sys as s; s.modules``,
  ``from sys import modules`` and ``m = sys; m.modules`` are all the same finding.
* A per-task *allowlist* of imports is the primary control. The built-in blocklist is a
  fallback for callers that do not supply one.
* Static analysis is defense in depth, not a security boundary: Python is too dynamic
  to make it airtight. The security boundary is the sandbox (see ``sandbox.py`` and
  ``docs/THREAT_MODEL.md``); tampering that evades this scan is still caught by the
  JUnit-report integrity checks in the evaluator.
"""

import ast
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set

MAX_SOURCE_BYTES = 200_000

# Module prefixes that a solution never needs. Matching is by dotted prefix.
FORBIDDEN_MODULES: Set[str] = {
    "unittest", "mock", "pytest", "_pytest", "pluggy",
    "importlib", "imp", "pkgutil", "runpy", "zipimport", "builtins",
    "ctypes", "cffi", "subprocess", "multiprocessing", "socket", "ssl",
    "http", "urllib", "ftplib", "smtplib", "requests", "asyncio.subprocess",
    "inspect", "gc", "atexit", "signal", "pdb", "code", "codeop", "marshal",
    "pickle", "shutil", "tempfile", "pathlib", "io", "os",
}

# Fully qualified names that are forbidden wherever they appear.
FORBIDDEN_QUALIFIED: Set[str] = {
    "sys.modules", "sys.meta_path", "sys.path_hooks", "sys.path_importer_cache",
    "sys._getframe", "sys._current_frames", "sys.exit", "sys.settrace",
    "sys.setprofile", "sys.excepthook", "sys.breakpointhook", "sys.stdout",
    "sys.stderr", "sys.__stdout__", "sys.__stderr__",
    "os._exit", "os.abort", "os.kill", "os.system", "os.popen", "os.fork",
}
FORBIDDEN_QUALIFIED_PREFIXES = ("os.exec", "os.spawn")

# Builtins that execute, load or introspect code or the filesystem.
FORBIDDEN_CALLS: Set[str] = {
    "exec", "eval", "compile", "__import__", "open", "input", "breakpoint",
    "globals", "locals", "vars", "setattr", "delattr", "exit", "quit",
}

# Attribute names used to escape the sandbox of the object model.
FORBIDDEN_ATTRS: Set[str] = {
    "__builtins__", "__globals__", "__code__", "__closure__", "__subclasses__",
    "__loader__", "__spec__", "__import__", "__mro__", "__bases__", "__base__",
    "__getattribute__", "f_globals", "f_locals", "f_back", "gi_frame", "tb_frame",
}

# Dunder methods that make an object impersonate expected values.
COMPARISON_DUNDERS = {"__eq__", "__ne__", "__bool__", "__lt__", "__le__", "__gt__", "__ge__"}
INTERCEPT_DUNDERS = {"__getattr__", "__getattribute__", "__setattr__", "__del__"}

_ALLOWED_TOPLEVEL = (
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Assign, ast.AnnAssign, ast.Pass,
)


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    lineno: int

    def __str__(self) -> str:
        return f"line {self.lineno}: [{self.rule}] {self.message}"


def _module_forbidden(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _qualified_forbidden(name: str) -> bool:
    return name in FORBIDDEN_QUALIFIED or name.startswith(FORBIDDEN_QUALIFIED_PREFIXES)


class ShortcutDetectionVisitor(ast.NodeVisitor):
    def __init__(self, allowed_imports: Optional[Sequence[str]] = None):
        self.allowed_imports = list(allowed_imports) if allowed_imports is not None else None
        self.aliases: Dict[str, str] = {}
        self._found: Dict[tuple, Violation] = {}

    # -- bookkeeping -------------------------------------------------------
    @property
    def violations(self) -> List[Violation]:
        return sorted(self._found.values(), key=lambda v: (v.lineno, v.rule, v.message))

    def _add(self, rule: str, message: str, node: ast.AST) -> None:
        lineno = getattr(node, "lineno", 0)
        self._found.setdefault((rule, message, lineno), Violation(rule, message, lineno))

    def _qualname(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._qualname(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def _import_allowed(self, module: str) -> bool:
        if self.allowed_imports is None:
            return True
        return any(module == a or module.startswith(a + ".") for a in self.allowed_imports)

    def _check_import(self, module: str, node: ast.AST) -> None:
        if _module_forbidden(module):
            self._add("AC001", f"forbidden module import: {module}", node)
        elif not self._import_allowed(module):
            self._add("AC002", f"import not in this task's allowlist: {module}", node)
        if _qualified_forbidden(module):
            self._add("AC003", f"forbidden runtime object: {module}", node)

    # -- imports -----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(alias.name, node)
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                root = alias.name.split(".")[0]
                self.aliases[root] = root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._add("AC004", "relative import is not allowed in a single-file submission", node)
        module = node.module or ""
        if module and module != "__future__":
            self._check_import(module, node)
        for alias in node.names:
            qual = f"{module}.{alias.name}" if module else alias.name
            if module and module != "__future__":
                if _module_forbidden(qual):
                    self._add("AC001", f"forbidden module import: {qual}", node)
                if _qualified_forbidden(qual):
                    self._add("AC003", f"forbidden runtime object: {qual}", node)
            self.aliases[alias.asname or alias.name] = qual
        self.generic_visit(node)

    # -- name / attribute resolution --------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        qual = self._qualname(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if qual:
                    self.aliases[target.id] = qual
                else:
                    self.aliases.pop(target.id, None)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in ("__builtins__", "__import__"):
            self._add("AC005", f"access to {node.id}", node)
        qual = self.aliases.get(node.id)
        if qual and _qualified_forbidden(qual):
            self._add("AC003", f"forbidden runtime object: {qual}", node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS:
            self._add("AC005", f"object-model escape via .{node.attr}", node)
        qual = self._qualname(node)
        if qual and _qualified_forbidden(qual):
            self._add("AC003", f"forbidden runtime object: {qual}", node)
        self.generic_visit(node)

    # -- calls -------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in FORBIDDEN_CALLS:
                self._add("AC006", f"forbidden builtin call: {func.id}()", node)
            elif func.id in ("getattr", "hasattr"):
                name_arg = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
                    self._add("AC007", f"dynamic attribute lookup via {func.id}()", node)
                elif name_arg.value in FORBIDDEN_ATTRS or name_arg.value in ("modules", "_getframe"):
                    self._add("AC007", f"{func.id}() of forbidden attribute '{name_arg.value}'", node)
        self.generic_visit(node)

    # -- definitions -------------------------------------------------------
    def _check_function(self, node) -> None:
        if node.name in INTERCEPT_DUNDERS:
            self._add("AC008", f"attribute-interception dunder {node.name} is not allowed", node)
        if node.name in COMPARISON_DUNDERS:
            returns = [n for n in _own_nodes(node) if isinstance(n, ast.Return)]
            if returns and all(isinstance(r.value, ast.Constant) for r in returns):
                self._add("AC008", f"{node.name} returns a constant (impersonates any value)", node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_body(node.body, f"class {node.name}")
        self.generic_visit(node)

    # -- import-time side effects -----------------------------------------
    def _check_body(self, body: Iterable[ast.stmt], where: str) -> None:
        for stmt in body:
            if isinstance(stmt, _ALLOWED_TOPLEVEL):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                continue  # docstring / Ellipsis
            if isinstance(stmt, ast.If) and _is_guard(stmt.test):
                continue
            self._add(
                "AC009",
                f"executable statement at {where} level ({type(stmt).__name__}); "
                "code must only define things at import time",
                stmt,
            )


def _is_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


def _own_nodes(func: ast.AST):
    """Yield nodes of ``func`` without descending into nested function/class scopes."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def scan_code(code_content: str, allowed_imports: Optional[Sequence[str]] = None) -> List[Violation]:
    """Parse ``code_content`` and return structured violations (empty list = clean)."""
    if len(code_content.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
        return [Violation("AC000", f"source exceeds {MAX_SOURCE_BYTES} bytes", 0)]
    try:
        tree = ast.parse(code_content)
    except SyntaxError as e:
        return [Violation("AC000", f"syntax error: {e.msg}", e.lineno or 0)]
    except (ValueError, RecursionError, MemoryError) as e:
        return [Violation("AC000", f"unparseable source ({type(e).__name__})", 0)]

    visitor = ShortcutDetectionVisitor(allowed_imports)
    try:
        visitor._check_body(tree.body, "module")
        visitor.visit(tree)
    except RecursionError:
        return [Violation("AC000", "source nesting too deep to analyse", 0)]
    return visitor.violations


def verify_code_integrity(
    code_content: str, allowed_imports: Optional[Sequence[str]] = None
) -> List[str]:
    """Return human-readable violation messages (empty list = clean)."""
    return [str(v) for v in scan_code(code_content, allowed_imports)]
