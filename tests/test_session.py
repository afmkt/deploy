"""Tests for deploy.session helpers."""

import pytest

from deploy.session import managed_connection


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
