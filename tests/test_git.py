import pytest
from deploy.git import GitRepository
from pathlib import Path
import os


def test_is_git_repo(tmp_path):
    # Create a fake git repo
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.is_git_repo() is True


def test_is_not_git_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.is_git_repo() is False


def test_get_repo_name(tmp_path):
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.get_repo_name() == "myrepo"


def test_validate_existing_git_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.validate() is True


def test_validate_nonexistent_path():
    repo = GitRepository("/nonexistent/path/for/test")
    assert repo.validate() is False


def test_validate_not_a_git_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = GitRepository(str(repo_dir))
    assert repo.validate() is False
