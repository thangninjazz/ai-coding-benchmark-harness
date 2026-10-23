"""The patch applier must be strict about what it accepts and agree with ``git apply``."""

import shutil
import subprocess

import pytest

from harness.workspace import PatchError, apply_patch, diff_trees, parse_unified_diff

EDITABLE = ["pkg/"]

ORIGINAL = "def add(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return -a\n"
PATCH = (
    "--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1,3 +1,3 @@\n"
    " def add(a, b):\n-    return a - b\n+    return a + b\n \n"
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(ORIGINAL)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "test_task.py").write_text("# protected\n")
    return tmp_path


def read(tree, rel="pkg/m.py"):
    return (tree / rel).read_text()


def test_applies_a_simple_patch(tree):
    assert apply_patch(tree, PATCH, EDITABLE) == ["pkg/m.py"]
    assert read(tree) == FIXED


def test_wrong_line_numbers_are_tolerated_when_context_matches(tree):
    shifted = PATCH.replace("@@ -1,3 +1,3 @@", "@@ -40,3 +40,3 @@")
    apply_patch(tree, shifted, EDITABLE)
    assert read(tree) == FIXED


def test_context_that_does_not_match_is_rejected_and_nothing_is_written(tree):
    bad = PATCH.replace("    return a - b", "    return a * b")
    with pytest.raises(PatchError, match="does not match"):
        apply_patch(tree, bad, EDITABLE)
    assert read(tree) == ORIGINAL


def test_multi_file_patch_is_all_or_nothing(tree):
    second = "--- a/pkg/missing.py\n+++ b/pkg/missing.py\n@@ -1 +1 @@\n-x\n+y\n"
    with pytest.raises(PatchError, match="does not exist"):
        apply_patch(tree, PATCH + second, EDITABLE)
    assert read(tree) == ORIGINAL


def test_creating_and_deleting_files(tree):
    create = "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+X = 1\n+Y = 2\n"
    assert apply_patch(tree, create, EDITABLE) == ["pkg/new.py"]
    assert read(tree, "pkg/new.py") == "X = 1\nY = 2\n"
    delete = "--- a/pkg/new.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-X = 1\n-Y = 2\n"
    apply_patch(tree, delete, EDITABLE)
    assert not (tree / "pkg" / "new.py").exists()


def test_creating_an_existing_file_is_rejected(tree):
    clash = "--- /dev/null\n+++ b/pkg/m.py\n@@ -0,0 +1 @@\n+X = 1\n"
    with pytest.raises(PatchError, match="already exists"):
        apply_patch(tree, clash, EDITABLE)


def test_no_newline_at_end_of_file_marker(tree):
    (tree / "pkg" / "n.py").write_text("A = 1\nB = 2")
    patch = (
        "--- a/pkg/n.py\n+++ b/pkg/n.py\n@@ -1,2 +1,2 @@\n A = 1\n-B = 2\n"
        "\\ No newline at end of file\n+B = 3\n\\ No newline at end of file\n"
    )
    apply_patch(tree, patch, EDITABLE)
    assert read(tree, "pkg/n.py") == "A = 1\nB = 3"


def test_crlf_patch_text_is_normalised(tree):
    apply_patch(tree, PATCH.replace("\n", "\r\n"), EDITABLE)
    assert read(tree) == FIXED


@pytest.mark.parametrize(
    "path,why",
    [
        ("/etc/passwd", "illegal path"),
        ("../outside.py", "traversal"),
        ("pkg/../../outside.py", "traversal"),
        ("C:/windows/x.py", "illegal path"),
        ("pkg\\m.py", "illegal path"),
        ("test_task.py", "outside the editable"),
        ("pkg/conftest.py", "loaded implicitly"),
        ("pkg/sitecustomize.py", "loaded implicitly"),
        ("pkg/evil.pth", "loaded implicitly"),
        ("pkg/data.txt", "only .py"),
        ("other/m.py", "outside the editable"),
    ],
)
def test_forbidden_paths_are_rejected(tree, path, why):
    patch = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+X = 1\n"
    with pytest.raises(PatchError, match=why):
        apply_patch(tree, patch, EDITABLE)


@pytest.mark.parametrize(
    "header",
    [
        "rename from pkg/m.py\nrename to pkg/n.py\n",
        "old mode 100644\nnew mode 100755\n",
        "new file mode 120000\n",
        "GIT binary patch\n",
    ],
)
def test_unsupported_git_features_are_rejected(tree, header):
    with pytest.raises(PatchError):
        apply_patch(tree, header + PATCH, EDITABLE)


def test_symlink_target_is_rejected(tree, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.py"
    outside.write_text("S = 1\n")
    link = tree / "pkg" / "link.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    patch = "--- a/pkg/link.py\n+++ b/pkg/link.py\n@@ -1 +1 @@\n-S = 1\n+S = 2\n"
    with pytest.raises(PatchError):
        apply_patch(tree, patch, EDITABLE)
    assert outside.read_text() == "S = 1\n"


def test_malformed_patches_are_rejected(tree):
    with pytest.raises(PatchError, match="without"):
        parse_unified_diff("--- a/pkg/m.py\n")
    with pytest.raises(PatchError, match="malformed hunk header"):
        parse_unified_diff("--- a/x\n+++ b/x\n@@ nonsense @@\n")
    with pytest.raises(PatchError, match="ended early"):
        parse_unified_diff("--- a/x\n+++ b/x\n@@ -1,5 +1,5 @@\n a\n")
    with pytest.raises(PatchError, match="exceeds"):
        parse_unified_diff("x" * 600_000)
    assert parse_unified_diff("just prose, no diff here") == []


def test_patching_the_same_file_twice_is_rejected(tree):
    with pytest.raises(PatchError, match="twice"):
        apply_patch(tree, PATCH + PATCH, EDITABLE)


def test_diff_trees_roundtrips_through_apply_patch(tree, tmp_path_factory):
    changed = tmp_path_factory.mktemp("changed")
    shutil.copytree(tree, changed, dirs_exist_ok=True)
    (changed / "pkg" / "m.py").write_text(FIXED)
    (changed / "pkg" / "extra.py").write_text("Z = 0\n")
    patch = diff_trees(tree, changed)
    apply_patch(tree, patch, EDITABLE)
    assert read(tree) == FIXED and read(tree, "pkg/extra.py") == "Z = 0\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_agrees_with_git_apply(tree, tmp_path_factory):
    git_tree = tmp_path_factory.mktemp("gitcopy")
    shutil.copytree(tree, git_tree, dirs_exist_ok=True)
    patch_file = tree.parent / "p.diff"
    patch_file.write_text(PATCH, newline="\n")
    subprocess.run(["git", "apply", str(patch_file)], cwd=git_tree, check=True)
    apply_patch(tree, PATCH, EDITABLE)
    assert read(tree) == (git_tree / "pkg" / "m.py").read_text() == FIXED
