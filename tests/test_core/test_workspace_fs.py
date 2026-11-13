"""O6 — Workspace filesystem isolation tests.

Unit tests for workspace_fs module: directory lifecycle, file tools,
and cross-workspace isolation.

Run with:
    python3 -m pytest tests/test_core/test_workspace_fs.py -v
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

# Add core/ to path so we can import workspace_fs directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))

import config as config_mod
import workspace_fs as ws_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def ws_root(tmp_path):
    """Patch settings.workspaces_data_dir to a temp dir for every test."""
    original = config_mod.settings.workspaces_data_dir
    config_mod.settings.workspaces_data_dir = str(tmp_path)
    yield tmp_path
    config_mod.settings.workspaces_data_dir = original


# ── O1: Directory creation ───────────────────────────────────────────────────

class TestDirectoryLifecycle:

    def test_ensure_creates_dir(self, ws_root):
        wid = str(uuid.uuid4())
        path = ws_mod.ensure_workspace_dir(wid)
        assert os.path.isdir(path)
        assert path == os.path.join(str(ws_root), wid)

    def test_ensure_idempotent(self, ws_root):
        wid = str(uuid.uuid4())
        p1 = ws_mod.ensure_workspace_dir(wid)
        p2 = ws_mod.ensure_workspace_dir(wid)
        assert p1 == p2
        assert os.path.isdir(p1)

    # ── O2: Archiving ────────────────────────────────────────────────────────

    def test_archive_renames_dir(self, ws_root):
        wid = str(uuid.uuid4())
        ws_mod.ensure_workspace_dir(wid)
        fpath = os.path.join(str(ws_root), wid, "test.txt")
        with open(fpath, "w") as f:
            f.write("hello")

        archived = ws_mod.archive_workspace_dir(wid)
        assert archived is not None
        assert archived.endswith("_archived")
        assert os.path.isdir(archived)
        assert not os.path.isdir(os.path.join(str(ws_root), wid))
        assert os.path.isfile(os.path.join(archived, "test.txt"))

    def test_archive_missing_dir_returns_none(self):
        assert ws_mod.archive_workspace_dir("nonexistent") is None


# ── O4/O5: File tools + path isolation ───────────────────────────────────────

class TestFileTools:

    @pytest.fixture(autouse=True)
    def setup_workspace(self, ws_root):
        self.wid = str(uuid.uuid4())
        ws_mod.ensure_workspace_dir(self.wid)
        self.read_file, self.write_file, self.list_files = ws_mod.make_workspace_fs_tools(self.wid)

    def test_write_and_read(self):
        result = self.write_file.invoke({"path": "hello.txt", "content": "world"})
        assert "File written" in result
        content = self.read_file.invoke({"path": "hello.txt"})
        assert content == "world"

    def test_write_creates_subdirs(self):
        result = self.write_file.invoke({"path": "sub/dir/file.txt", "content": "nested"})
        assert "File written" in result
        content = self.read_file.invoke({"path": "sub/dir/file.txt"})
        assert content == "nested"

    def test_list_files(self):
        self.write_file.invoke({"path": "a.txt", "content": "a"})
        self.write_file.invoke({"path": "b.txt", "content": "b"})
        self.write_file.invoke({"path": "sub/c.txt", "content": "c"})
        listing = self.list_files.invoke({"path": "."})
        assert "a.txt" in listing
        assert "b.txt" in listing
        assert "sub/" in listing

    def test_list_empty_dir(self):
        listing = self.list_files.invoke({"path": "."})
        assert listing == "(empty directory)"

    def test_read_nonexistent(self):
        result = self.read_file.invoke({"path": "nope.txt"})
        assert "Error" in result

    def test_list_nonexistent_dir(self):
        result = self.list_files.invoke({"path": "nope"})
        assert "Error" in result

    # ── O5: Path traversal prevention ────────────────────────────────────────

    def test_traversal_dotdot_blocked(self):
        result = self.read_file.invoke({"path": "../../etc/passwd"})
        assert "Access denied" in result

    def test_traversal_write_blocked(self):
        result = self.write_file.invoke({"path": "../other_ws/evil.txt", "content": "hacked"})
        assert "Access denied" in result

    def test_traversal_list_blocked(self):
        result = self.list_files.invoke({"path": "../"})
        assert "Access denied" in result

    def test_absolute_path_blocked(self):
        result = self.read_file.invoke({"path": "/../../../etc/passwd"})
        assert "Access denied" in result


# ── O6: Cross-workspace isolation ────────────────────────────────────────────

class TestCrossWorkspaceIsolation:

    @pytest.fixture(autouse=True)
    def setup_two_workspaces(self, ws_root):
        self.wid_a = str(uuid.uuid4())
        self.wid_b = str(uuid.uuid4())
        ws_mod.ensure_workspace_dir(self.wid_a)
        ws_mod.ensure_workspace_dir(self.wid_b)

        self.read_a, self.write_a, self.list_a = ws_mod.make_workspace_fs_tools(self.wid_a)
        self.read_b, self.write_b, self.list_b = ws_mod.make_workspace_fs_tools(self.wid_b)

    def test_write_in_a_read_in_a(self):
        """Agent in workspace A can read files written by another agent in workspace A."""
        self.write_a.invoke({"path": "shared.txt", "content": "from A"})
        content = self.read_a.invoke({"path": "shared.txt"})
        assert content == "from A"

    def test_write_in_a_not_visible_in_b(self):
        """Agent in workspace B cannot read files from workspace A."""
        self.write_a.invoke({"path": "secret.txt", "content": "A data"})
        result = self.read_b.invoke({"path": "secret.txt"})
        assert "Error" in result

    def test_b_cannot_traverse_to_a(self):
        """Agent in workspace B cannot traverse to workspace A's directory."""
        self.write_a.invoke({"path": "secret.txt", "content": "A data"})
        result = self.read_b.invoke({"path": f"../{self.wid_a}/secret.txt"})
        assert "Access denied" in result

    def test_independent_namespaces(self):
        """Same filename in both workspaces has independent content."""
        self.write_a.invoke({"path": "data.txt", "content": "workspace A"})
        self.write_b.invoke({"path": "data.txt", "content": "workspace B"})
        assert self.read_a.invoke({"path": "data.txt"}) == "workspace A"
        assert self.read_b.invoke({"path": "data.txt"}) == "workspace B"

    def test_list_only_own_files(self):
        """list_files only shows files from the agent's own workspace."""
        self.write_a.invoke({"path": "only_in_a.txt", "content": "a"})
        self.write_b.invoke({"path": "only_in_b.txt", "content": "b"})
        listing_a = self.list_a.invoke({"path": "."})
        listing_b = self.list_b.invoke({"path": "."})
        assert "only_in_a.txt" in listing_a
        assert "only_in_b.txt" not in listing_a
        assert "only_in_b.txt" in listing_b
        assert "only_in_a.txt" not in listing_b
