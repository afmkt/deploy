"""Tests for deploy.session helpers."""

import pytest

from deploy.local import LocalConnection
from deploy.session import connection_args_from_connection, managed_connection
from deploy.ssh import SSHConnection


class _FakeConnection:
    def __init__(self, connect_result: bool = True):
        self.connect_result = connect_result
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_result

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_managed_connection_connects_and_disconnects():
    conn = _FakeConnection(connect_result=True)

    with managed_connection(conn) as active:
        assert active is conn
        assert conn.connect_calls == 1
        assert conn.disconnect_calls == 0

    assert conn.disconnect_calls == 1


def test_managed_connection_raises_on_connect_failure():
    conn = _FakeConnection(connect_result=False)

    with pytest.raises(ConnectionError):
        with managed_connection(conn):
            pass

    assert conn.connect_calls == 1
    assert conn.disconnect_calls == 0


def test_connection_args_from_connection_for_local_target():
    conn = LocalConnection(username="tester", key_filename="/tmp/key")

    assert connection_args_from_connection(conn) == {
        "host": "local",
        "port": 0,
        "username": "tester",
        "key": "/tmp/key",
        "target": "local",
    }


def test_connection_args_from_connection_for_remote_target():
    conn = SSHConnection(host="example.com", port=2222, username="root", key_filename="/tmp/key")

    assert connection_args_from_connection(conn) == {
        "host": "example.com",
        "port": 2222,
        "username": "root",
        "key": "/tmp/key",
        "target": "remote",
    }
