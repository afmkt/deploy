from types import SimpleNamespace

from deploy.config import DeployConfig
from deploy.session import (
    ConnectionProfile,
    connection_args,
    connection_args_from_connection,
    load_connection_profile,
)


def test_connection_args_persist_remote_key():
    profile = ConnectionProfile(host="example.com", port=2222, username="alice", key="~/.ssh/id")

    assert connection_args(profile) == {
        "remote": "example.com",
        "port": 2222,
        "username": "alice",
        "key": "~/.ssh/id",
    }


def test_connection_args_from_connection_persist_remote_key():
    connection = SimpleNamespace(host="example.com", port=22, username="alice", key_filename="/tmp/key")

    assert connection_args_from_connection(connection) == {
        "remote": "example.com",
        "port": 22,
        "username": "alice",
        "key": "/tmp/key",
    }


def test_load_connection_profile_falls_back_to_repo_push(tmp_path):
    config = DeployConfig(config_dir=tmp_path)
    config.save_args(
        {"remote": "fallback.example.com", "port": 22, "username": "alice", "key": "/tmp/key"},
        "repo.push",
    )

    result = load_connection_profile(
        config,
        "image.push",
        ConnectionProfile(),
        use_config=True,
        fallback_sources=("repo.push",),
    )

    assert result.profile.host == "fallback.example.com"
    assert result.profile.username == "alice"
    assert result.fallback_source == "repo.push"
