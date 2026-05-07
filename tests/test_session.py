from types import SimpleNamespace

from deploy.session import (
    ConnectionProfile,
    connection_args,
    connection_args_from_connection,
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

