import pytest
from deploy.git import GitRepository
from pathlib import Path
import os
import subprocess

def test_get_remote_url_none(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.get_remote_url("nonexistent") is None

def test_add_remote_existing(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    monkeypatch.setattr(repo, "get_remote_url", lambda name: "url")
    assert repo.add_remote("origin", "url")


def test_checkout_branch_create_uses_existing_branch(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    calls = []

    def fake_run(args, cwd=None, check=False, capture_output=False, text=False):
        calls.append(args)
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert repo.checkout_branch("main", create=True)
    assert calls == [["git", "checkout", "main"]]


def test_checkout_branch_create_falls_back_to_new_branch(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    calls = []

    def fake_run(args, cwd=None, check=False, capture_output=False, text=False):
        calls.append(args)
        if args == ["git", "checkout", "feature"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=args)
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert repo.checkout_branch("feature", create=True)
    assert calls == [
        ["git", "checkout", "feature"],
        ["git", "checkout", "-b", "feature"],
    ]


def test_has_uncommitted_changes_true(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))

    class Result:
        stdout = " M file.txt\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert repo.has_uncommitted_changes() is True


def test_has_uncommitted_changes_error_is_conservative(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))

    def fail_run(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "status", "--porcelain"])

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert repo.has_uncommitted_changes() is True
