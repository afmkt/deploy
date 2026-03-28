import pytest
from deploy.git import GitRepository
from pathlib import Path
import os

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
