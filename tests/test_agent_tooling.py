"""The agent worktree and validation scripts, against a throwaway clone.

Nothing here touches the real repository: each test clones it into a
temporary directory and runs the scripts there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKTREE = REPO / "scripts" / "agent_worktree.sh"
VALIDATE = REPO / "scripts" / "agent_validate.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


def _run(args, cwd, env=None):
    return subprocess.run(["bash", *map(str, args)], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=300)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def clone(tmp_path):
    target = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(REPO), str(target)], check=True,
                   timeout=300)
    _git(target, "config", "user.email", "agent@example.invalid")
    _git(target, "config", "user.name", "Agent Test")
    _git(target, "branch", "-f", "main", "HEAD")
    env = {"LOSSLIFT_WORKTREES": str(tmp_path / "worktrees")}
    return target, env, tmp_path / "worktrees"


def test_create_makes_a_deterministic_isolated_branch_and_worktree(clone):
    repo, env, trees = clone
    made = _run([WORKTREE, "create", "fix-r27", "--agent", "alice", "--base", "main"], repo, env)
    assert made.returncode == 0, made.stderr
    path = trees / "alice--fix-r27"
    assert path.is_dir()
    assert _git(path, "symbolic-ref", "--short", "HEAD") == "agent/alice/fix-r27"
    assert _git(repo, "config", "--get", "branch.agent/alice/fix-r27.agentbase") \
        == _git(repo, "rev-parse", "main")
    again = _run([WORKTREE, "create", "fix-r27", "--agent", "alice"], repo, env)
    assert again.returncode != 0 and "already exists" in again.stderr


@pytest.mark.parametrize("slug", ["Bad Slug", "../escape", "", "UPPER"])
def test_create_refuses_unsafe_names(clone, slug):
    repo, env, trees = clone
    made = _run([WORKTREE, "create", slug, "--agent", "alice"], repo, env)
    assert made.returncode != 0
    assert not trees.exists() or not any(trees.iterdir())


def test_check_refuses_the_main_worktree_and_shared_branches(clone):
    repo, env, trees = clone
    assert _run([WORKTREE, "check"], repo, env).returncode != 0
    _run([WORKTREE, "create", "t1", "--agent", "bob"], repo, env)
    path = trees / "bob--t1"
    assert _run([WORKTREE, "check"], path, env).returncode == 0
    _git(path, "checkout", "-q", "-b", "shared-branch")
    shared = _run([WORKTREE, "check"], path, env)
    assert shared.returncode != 0 and "not an agent branch" in shared.stderr


def test_remove_refuses_uncommitted_work_and_keeps_the_branch(clone):
    repo, env, trees = clone
    _run([WORKTREE, "create", "t2", "--agent", "bob"], repo, env)
    path = trees / "bob--t2"
    (path / "scratch.txt").write_text("work in progress")
    refused = _run([WORKTREE, "remove", "t2", "--agent", "bob"], repo, env)
    assert refused.returncode != 0 and "uncommitted" in refused.stderr and path.is_dir()
    (path / "scratch.txt").unlink()
    removed = _run([WORKTREE, "remove", "t2", "--agent", "bob"], repo, env)
    assert removed.returncode == 0, removed.stderr
    assert not path.exists()
    assert _git(repo, "branch", "--list", "agent/bob/t2")


def test_validate_passes_a_clean_agent_worktree(clone):
    repo, env, trees = clone
    _run([WORKTREE, "create", "t3", "--agent", "carol"], repo, env)
    path = trees / "carol--t3"
    (path / "notes.md").write_text("a harmless change\n")
    _git(path, "add", "notes.md")
    _git(path, "commit", "-q", "-m", "Add notes")
    checked = _run([VALIDATE, "--quick"], path, env)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "PASS     private-data" in checked.stdout
    assert "NOT RUN  corpus-gate" in checked.stdout


@pytest.mark.parametrize("name", ["loss_run.pdf", "data/telemetry/events.jsonl",
                                  "corpus_manifest.json", ".env"])
def test_validate_fails_on_private_data_or_documents(clone, name):
    repo, env, trees = clone
    _run([WORKTREE, "create", "t4", "--agent", "dave"], repo, env)
    path = trees / "dave--t4"
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic stand-in\n")
    _git(path, "add", "-f", name)
    _git(path, "commit", "-q", "-m", "Add a file that must not be committed")
    checked = _run([VALIDATE, "--quick"], path, env)
    assert checked.returncode != 0
    assert "FAIL     private-data" in checked.stdout


def test_validate_fails_outside_an_agent_worktree(clone):
    repo, env, _trees = clone
    checked = _run([VALIDATE, "--quick"], repo, env)
    assert checked.returncode != 0 and "FAIL     worktree" in checked.stdout
    allowed = _run([VALIDATE, "--quick", "--allow-main"], repo, env)
    assert "WARN     worktree" in allowed.stdout


def test_validate_fails_on_uncommitted_changes(clone):
    repo, env, trees = clone
    _run([WORKTREE, "create", "t5", "--agent", "erin"], repo, env)
    path = trees / "erin--t5"
    (path / "README.md").write_text((path / "README.md").read_text() + "\nlocal edit\n")
    checked = _run([VALIDATE, "--quick"], path, env)
    assert checked.returncode != 0 and "FAIL     uncommitted" in checked.stdout
