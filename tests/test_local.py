"""Tests for local execution target support."""

from deploy.local import LocalConnection
from deploy.remote import RemoteServer


def test_local_connection_executes_shell_command():
    conn = LocalConnection(username="tester")
    assert conn.connect() is True
    try:
        exit_code, stdout, stderr = conn.execute("printf 'hello'")
        assert exit_code == 0
        assert stdout == "hello"
        assert stderr == ""
    finally:
        conn.disconnect()


def test_remote_server_setup_deployment_returns_local_path_for_local_target(tmp_path):
    conn = LocalConnection(username="tester")
    assert conn.connect() is True
    try:
        remote = RemoteServer(conn, str(tmp_path / "repos"))
        success, bare_repo_url = remote.setup_deployment("demo")
        assert success is True
        assert bare_repo_url == str(tmp_path / "repos" / "demo.git")
    finally:
        conn.disconnect()