"""Tests for ACEPRO driver version reporting (ace/version.py)."""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ace.version import get_driver_version, _read_git_files


class TestReadGitFiles:
    """File-based fallback parser (no git binary required)."""

    def _make_repo(self, tmp_path, head, refs=None, packed=None):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(head)
        for ref_path, sha in (refs or {}).items():
            ref_file = git_dir / ref_path
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            ref_file.write_text(sha + "\n")
        if packed is not None:
            (git_dir / "packed-refs").write_text(packed)
        return str(tmp_path)

    def test_branch_with_loose_ref(self, tmp_path):
        root = self._make_repo(
            tmp_path,
            head="ref: refs/heads/dev\n",
            refs={"refs/heads/dev": "abcdef1234567890"},
        )
        commit, branch = _read_git_files(root)
        assert commit == "abcdef12"
        assert branch == "dev"

    def test_branch_with_packed_ref(self, tmp_path):
        root = self._make_repo(
            tmp_path,
            head="ref: refs/heads/feature/x\n",
            packed=(
                "# pack-refs with: peeled fully-peeled sorted\n"
                "1111222233334444 refs/heads/main\n"
                "aaaabbbbccccdddd refs/heads/feature/x\n"
            ),
        )
        commit, branch = _read_git_files(root)
        assert commit == "aaaabbbb"
        assert branch == "feature/x"

    def test_detached_head(self, tmp_path):
        root = self._make_repo(tmp_path, head="fedcba9876543210\n")
        commit, branch = _read_git_files(root)
        assert commit == "fedcba98"
        assert branch == "detached"

    def test_no_git_dir_returns_none(self, tmp_path):
        assert _read_git_files(str(tmp_path)) is None


class TestGetDriverVersion:

    def test_unknown_for_non_repo(self, tmp_path):
        version = get_driver_version(repo_root=str(tmp_path))
        assert version.startswith("unknown")
        assert str(tmp_path) in version

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_real_repo_reports_commit_and_branch(self):
        # The default repo root is this checkout itself.
        version = get_driver_version()
        # "abc1234 (branch) at /path" or with ", dirty"
        assert "(" in version and ")" in version
        commit = version.split(" ", 1)[0]
        assert len(commit) >= 7
        int(commit, 16)  # short sha is hex

    def test_file_fallback_used_without_git(self, tmp_path, monkeypatch):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/dev\n")
        ref = git_dir / "refs" / "heads" / "dev"
        ref.parent.mkdir(parents=True)
        ref.write_text("abcdef1234567890\n")

        # Simulate git binary missing
        import ace.version as version_mod
        monkeypatch.setattr(
            version_mod, "_read_git_subprocess", lambda root: None
        )

        version = get_driver_version(repo_root=str(tmp_path))
        assert version.startswith("abcdef12 (dev)")
