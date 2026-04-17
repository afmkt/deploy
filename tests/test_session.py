"""Tests for deploy.session helpers."""

import pytest
from unittest.mock import Mock

from deploy.local import LocalConnection
from deploy.session import ConnectionProfile, complete_connection_profile, connection_args_from_connection, managed_connection
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
    }


def test_connection_args_from_connection_for_remote_target():
    conn = SSHConnection(host="example.com", port=2222, username="root", key_filename="/tmp/key")

    assert connection_args_from_connection(conn) == {
        "host": "example.com",
        "port": 2222,
        "username": "root",
        "key": "/tmp/key",
    }


def test_complete_connection_profile_prompts_for_missing_host_and_resolves_local(monkeypatch):
    prompt_details = Mock(side_effect=AssertionError("prompt_connection_details should not be called for local host"))

    monkeypatch.setattr("deploy.session.Prompt.ask", lambda *args, **kwargs: "localhost")
    monkeypatch.setattr("deploy.session.prompt_connection_details", prompt_details)

    resolved = complete_connection_profile(ConnectionProfile(), interactive=True)

    assert resolved is not None
    assert resolved.mode == "local"
    assert resolved.host == "localhost"


def test_complete_connection_profile_uses_explicit_remote_host_prompt(monkeypatch):
    prompt_details = Mock(side_effect=AssertionError("prompt_connection_details should not be called for local host"))
    prompt_calls = []

    def fake_prompt(label, **kwargs):
        prompt_calls.append((label, kwargs.get("default")))
        return "localhost"

    monkeypatch.setattr("deploy.session.Prompt.ask", fake_prompt)
    monkeypatch.setattr("deploy.session.prompt_connection_details", prompt_details)

    resolved = complete_connection_profile(ConnectionProfile(), interactive=True)

    assert resolved is not None
    assert prompt_calls == [("Remote host (or localhost)", "localhost")]


def test_complete_connection_profile_prompts_for_missing_remote_identity(monkeypatch):
    monkeypatch.setattr("deploy.session.Prompt.ask", lambda *args, **kwargs: "example.com")
    details_mock = Mock(return_value={
        "host": "example.com",
        "port": 22,
        "username": "root",
        "key_filename": "/tmp/id_rsa",
        "password": None,
    })
    monkeypatch.setattr("deploy.session.prompt_connection_details", details_mock)

    resolved = complete_connection_profile(ConnectionProfile(), interactive=True)

    assert resolved is not None
    assert resolved.mode == "remote"
    assert resolved.host == "example.com"
    assert resolved.username == "root"
    details_mock.assert_called_once_with("example.com", 22)
