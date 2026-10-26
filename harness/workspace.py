"""
Staging a candidate into a work directory.

A candidate is either

* ``single_file`` tasks: the source of ``implementation.py``; or
* ``patch`` tasks (SWE-bench style): a unified diff against the task's ``repo/`` snapshot.

Patches are applied by a small, strict, pure-Python implementation instead of ``git apply``
so that behaviour is identical on every host, the sandbox image does not need ``git``, and
the harness decides exactly which paths a patch may touch:

* only ``.py`` files under the task's ``editable_paths``;
* no absolute paths, ``..`` traversal, symlinks, renames or mode changes;
* never ``conftest.py`` / ``sitecustomize.py`` / ``*.pth`` (files Python or pytest load
  implicitly), whatever directory they are in.

Hunks must match their context exactly; line numbers may be off (the nearest exact match
is used), which is the common failure of model-written diffs. No fuzzy matching.
"""

import difflib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

from harness.anti_cheat import scan_code
from harness.models import TaskDefinition

MAX_PATCH_BYTES = 500_000
MAX_FILE_BYTES = 2_000_000
_FORBIDDEN_BASENAMES = {"conftest.py", "sitecustomize.py", "usercustomize.py"}
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_UNSUPPORTED_HEADERS = ("rename from", "rename to", "copy from", "copy to", "old mode", "new mode",
                        "GIT binary patch", "Binary files")


class PatchError(Exception):
    """The patch is malformed or not allowed; the candidate is scored as unresolved."""


@dataclass
class Hunk:
    old_start: int
    old: List[str]
    new: List[str]
    new_no_eol: bool = False
    old_no_eol: bool = False


@dataclass
class FilePatch:
    old_path: Optional[str]
    new_path: Optional[str]
    hunks: List[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


# --------------------------------------------------------------------------- parsing
def _clean_path(raw: str) -> Optional[str]:
    raw = raw.split("\t")[0].strip()
    if raw == "/dev/null":
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def parse_unified_diff(text: str) -> List[FilePatch]:
    if len(text.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES:
        raise PatchError(f"patch exceeds {MAX_PATCH_BYTES} bytes")
    lines = text.replace("\r\n", "\n").split("\n")
    patches: List[FilePatch] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(_UNSUPPORTED_HEADERS):
            raise PatchError(f"unsupported patch feature: {line.split(' ', 2)[0]} {line.split(' ', 2)[1:2]}")
        if line.startswith(("new file mode 120000", "deleted file mode 120000")) or "mode 120000" in line:
            raise PatchError("symlinks are not allowed")
        if line.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchError(f"line {i + 1}: '---' header without '+++' header")
            fp = FilePatch(_clean_path(line[4:]), _clean_path(lines[i + 1][4:]))
            i += 2
            while i < len(lines) and lines[i].startswith("@@"):
                hunk, i = _parse_hunk(lines, i)
                fp.hunks.append(hunk)
            if not fp.hunks:
                raise PatchError(f"{fp.path}: no hunks")
            patches.append(fp)
            continue
        i += 1
    return patches


def _parse_hunk(lines: List[str], i: int) -> Tuple[Hunk, int]:
    m = _HUNK_RE.match(lines[i])
    if not m:
        raise PatchError(f"line {i + 1}: malformed hunk header {lines[i]!r}")
    old_start, old_len = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
    new_len = int(m.group(4) if m.group(4) is not None else 1)
    i += 1
    hunk = Hunk(old_start, [], [])
    last = " "
    while (len(hunk.old) < old_len or len(hunk.new) < new_len) or (
        i < len(lines) and lines[i].startswith("\\")
    ):
        if i >= len(lines):
            raise PatchError("hunk ended early: fewer lines than its header declares")
        line = lines[i]
        i += 1
        if line.startswith("\\"):
            if last == "-":
                hunk.old_no_eol = True
            elif last == "+":
                hunk.new_no_eol = True
            else:
                hunk.old_no_eol = hunk.new_no_eol = True
            continue
        tag, body = (line[:1] or " "), line[1:]
        last = tag
        if tag == " ":
            hunk.old.append(body)
            hunk.new.append(body)
        elif tag == "-":
            hunk.old.append(body)
        elif tag == "+":
            hunk.new.append(body)
        else:
            raise PatchError(f"line {i}: unexpected {line[:20]!r} inside a hunk")
    if len(hunk.old) != old_len or len(hunk.new) != new_len:
        raise PatchError("hunk line counts do not match its header")
    return hunk, i


# -------------------------------------------------------------------------- validation
def _validate_path(path: str, editable: Sequence[str]) -> str:
    if not path or "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise PatchError(f"illegal path {path!r}")
    parts = PurePosixPath(path).parts
    if ".." in parts or "." in parts[:-1]:
        raise PatchError(f"path traversal in {path!r}")
    norm = str(PurePosixPath(*parts))
    if parts[-1] in _FORBIDDEN_BASENAMES or norm.endswith(".pth"):
        raise PatchError(f"{norm}: this file is loaded implicitly and may not be modified")
    if not norm.endswith(".py"):
        raise PatchError(f"{norm}: only .py files may be modified")
    if not any(norm == e or norm.startswith(e if e.endswith("/") else e + "/") for e in editable):
        raise PatchError(f"{norm}: outside the editable paths {list(editable)}")
    return norm


# --------------------------------------------------------------------------- applying
def _split(content: str) -> Tuple[List[str], bool]:
    if content == "":
        return [], True
    ends = content.endswith("\n")
    return (content[:-1] if ends else content).split("\n"), ends


def _join(lines: List[str], ends: bool) -> str:
    return "" if not lines else "\n".join(lines) + ("\n" if ends else "")


def _apply_hunks(path: str, content: str, hunks: List[Hunk]) -> str:
    lines, ends = _split(content)
    offset = 0
    floor = 0
    for hunk in hunks:
        want = hunk.old
        expected = max(0, hunk.old_start - 1 + offset)
        if not want:  # pure insertion: "-N,0" inserts after line N
            pos = min(max(0, hunk.old_start + offset), len(lines))
        else:
            candidates = [
                p for p in range(floor, len(lines) - len(want) + 1) if lines[p:p + len(want)] == want
            ]
            if not candidates:
                raise PatchError(f"{path}: hunk at line {hunk.old_start} does not match the file")
            pos = min(candidates, key=lambda p: abs(p - expected))
        lines[pos:pos + len(want)] = hunk.new
        offset += len(hunk.new) - len(want)
        floor = pos + len(hunk.new)
        if pos + len(hunk.new) >= len(lines):  # hunk reaches end of file
            ends = not hunk.new_no_eol if (hunk.new or hunk.old_no_eol) else ends
    return _join(lines, ends)


def apply_patch(root: Path, text: str, editable: Sequence[str]) -> List[str]:
    """Apply ``text`` to the tree at ``root``. All-or-nothing; returns changed paths."""
    root = root.resolve()
    staged: Dict[str, Optional[str]] = {}
    for fp in parse_unified_diff(text):
        if fp.old_path is None and fp.new_path is None:
            raise PatchError("patch has neither old nor new path")
        if fp.old_path is not None and fp.new_path is not None and fp.old_path != fp.new_path:
            raise PatchError("renames are not supported")
        rel = _validate_path(fp.path, editable)
        target = (root / rel).resolve()
        if root not in target.parents:
            raise PatchError(f"{rel}: escapes the workspace")
        if rel in staged:
            raise PatchError(f"{rel}: patched twice")
        exists = target.is_file()
        if target.is_symlink() or (target.exists() and not exists):
            raise PatchError(f"{rel}: not a regular file")
        if fp.old_path is None:
            if exists:
                raise PatchError(f"{rel}: patch creates a file that already exists")
            staged[rel] = _apply_hunks(rel, "", fp.hunks)
        else:
            if not exists:
                raise PatchError(f"{rel}: file to patch does not exist")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise PatchError(f"{rel}: file too large")
            new = _apply_hunks(rel, target.read_text(encoding="utf-8"), fp.hunks)
            if fp.new_path is None:
                if new != "":
                    raise PatchError(f"{rel}: deletion hunk does not remove the whole file")
                staged[rel] = None
            else:
                staged[rel] = new
        content = staged[rel]
        if content is not None and len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise PatchError(f"{rel}: result too large")
    for rel, content in staged.items():
        target = root / rel
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    return sorted(staged)


# ----------------------------------------------------------------------------- diffing
def _py_files(root: Path) -> Dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


def diff_trees(old: Path, new: Path) -> str:
    """Unified diff turning the ``.py`` files of ``old`` into those of ``new``."""
    a, b = _py_files(old), _py_files(new)
    chunks: List[str] = []
    for rel in sorted(set(a) | set(b)):
        if a.get(rel) == b.get(rel):
            continue
        before = a[rel].splitlines(keepends=True) if rel in a else []
        after = b[rel].splitlines(keepends=True) if rel in b else []
        chunks.extend(difflib.unified_diff(
            before, after,
            fromfile=f"a/{rel}" if rel in a else "/dev/null",
            tofile=f"b/{rel}" if rel in b else "/dev/null",
        ))
    return "".join(chunks)


# ----------------------------------------------------------------------------- staging
@dataclass
class Staged:
    violations: List[str] = field(default_factory=list)
    error: Optional[str] = None
    changed: List[str] = field(default_factory=list)


def stage_workspace(task: TaskDefinition, candidate: str, workdir: Path) -> Staged:
    """Materialise ``candidate`` for ``task`` into ``workdir`` and run the static scan."""
    meta = task.meta
    if meta.kind == "single_file":
        (workdir / "implementation.py").write_text(candidate, encoding="utf-8", newline="\n")
        found = scan_code(candidate, meta.allowed_imports)
        return Staged(violations=[str(v) for v in found], changed=["implementation.py"])

    shutil.copytree(
        task.path / "repo", workdir, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if not candidate.strip():
        return Staged()
    try:
        changed = apply_patch(workdir, candidate, meta.editable_paths)
    except PatchError as e:
        return Staged(error=f"patch rejected: {e}")
    except UnicodeDecodeError:
        return Staged(error="patch rejected: target file is not valid UTF-8")
    violations: List[str] = []
    for rel in changed:
        path = workdir / rel
        if path.exists():
            for v in scan_code(path.read_text(encoding="utf-8"), meta.allowed_imports, allow_relative=True):
                violations.append(f"{rel}: {v}")
    return Staged(violations=violations, changed=changed)
