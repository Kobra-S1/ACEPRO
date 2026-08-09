"""ACEPRO driver version reporting.

Klippy logs its own repo's git version at startup, but the ACEPRO driver
lives in a separate repo symlinked into klippy/extras — support logs
carried no clue which driver commit produced them.  This module resolves
the driver's own checkout (following the installer's symlink) and
formats a one-line version string for klippy.log and bug reports.
"""

import os
import subprocess

_GIT_TIMEOUT_S = 5


def _default_repo_root():
    """The ACEPRO checkout containing this file, symlinks resolved."""
    here = os.path.realpath(__file__)          # <repo>/extras/ace/version.py
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _read_git_subprocess(repo_root):
    """Ask the git binary. Returns (commit, branch, dirty) or None."""
    def _git(*args):
        return subprocess.check_output(
            ["git", "-C", repo_root] + list(args),
            stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT_S,
        ).decode("utf-8", "replace").strip()

    try:
        commit = _git("rev-parse", "--short", "HEAD")
        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
        return commit, branch, dirty
    except Exception:
        return None


def _read_git_files(repo_root):
    """Parse .git/HEAD directly (no git binary, no dirty detection).

    Returns (commit, branch) or None.
    """
    git_dir = os.path.join(repo_root, ".git")
    try:
        with open(os.path.join(git_dir, "HEAD")) as head_file:
            head = head_file.read().strip()

        if not head.startswith("ref: "):
            return head[:8], "detached"

        ref = head[5:]
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") \
            else ref.rsplit("/", 1)[-1]

        ref_file = os.path.join(git_dir, *ref.split("/"))
        if os.path.exists(ref_file):
            with open(ref_file) as f:
                return f.read().strip()[:8], branch

        packed = os.path.join(git_dir, "packed-refs")
        if os.path.exists(packed):
            with open(packed) as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0][:8], branch
        return None
    except Exception:
        return None


def get_driver_version(repo_root=None):
    """One-line driver version: ``<commit> (<branch>[, dirty]) at <path>``.

    Falls back to parsing .git files when the git binary is unavailable
    (dirty state then unknown and omitted), and to ``unknown at <path>``
    when the checkout carries no readable git metadata at all.
    """
    root = repo_root or _default_repo_root()

    info = _read_git_subprocess(root)
    if info is not None:
        commit, branch, dirty = info
        dirty_suffix = ", dirty" if dirty else ""
        return f"{commit} ({branch}{dirty_suffix}) at {root}"

    file_info = _read_git_files(root)
    if file_info is not None:
        commit, branch = file_info
        return f"{commit} ({branch}) at {root}"

    return f"unknown at {root}"
