import socket
import pytest
from deploy.ssh import SSHConnection


def test_ssh_connect_fail(monkeypatch):
    ssh = SSHConnection(host="badhost", port=22, username="user", password="pass")
    # Patch paramiko to always raise exception
    class DummyClient:
        def set_missing_host_key_policy(self, policy):
            pass
        def connect(self, **kwargs):
            raise Exception("fail connect")
        def close(self):
            pass
    monkeypatch.setattr("paramiko.SSHClient", lambda: DummyClient())
    assert not ssh.connect()


def test_ssh_connect_timeout_message_includes_target(monkeypatch):
    ssh = SSHConnection(
        host="47.100.30.18",
        port=22,
        username="root",
        key_filename="/Users/michael/.ssh/id_rsa",
    )
    messages = []

    class DummyClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise socket.timeout("timed out")

        def close(self):
            pass

    monkeypatch.setattr("paramiko.SSHClient", lambda: DummyClient())
    monkeypatch.setattr("deploy.ssh.console.print", lambda message: messages.append(str(message)))
    assert not ssh.connect()
    assert any("root@47.100.30.18:22" in message for message in messages)
    assert any("/Users/michael/.ssh/id_rsa" in message for message in messages)


def test_ssh_auth_failure_message_includes_target(monkeypatch):
    ssh = SSHConnection(host="47.100.30.18", port=22, username="root", password="secret")
    messages = []

    class DummyClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise __import__("paramiko").AuthenticationException()

        def close(self):
            pass

    monkeypatch.setattr("paramiko.SSHClient", lambda: DummyClient())
    monkeypatch.setattr("deploy.ssh.console.print", lambda message: messages.append(str(message)))
    assert not ssh.connect()
    assert any("root@47.100.30.18:22" in message for message in messages)
    assert any("password" in message for message in messages)


def test_ssh_execute_not_connected():
    ssh = SSHConnection(host="localhost", port=22, username="user")
    code, out, err = ssh.execute("ls")
    assert code == -1
    assert err == "Not connected"


def test_ssh_context_manager(monkeypatch):
    ssh = SSHConnection(host="localhost", port=22, username="user")
    class DummyClient:
        def set_missing_host_key_policy(self, policy):
            pass
        def connect(self, **kwargs):
            pass
        def close(self):
            ssh.closed = True
    monkeypatch.setattr("paramiko.SSHClient", lambda: DummyClient())
    ssh.closed = False
    with ssh:
        pass
    assert ssh.closed


def test_ssh_execute_uses_default_command_timeout(monkeypatch):
    ssh = SSHConnection(host="localhost", port=22, username="user", command_timeout=9)

    class DummyStdout:
        def __init__(self):
            self.channel = self

        def settimeout(self, timeout):
            self.timeout = timeout

        def recv_exit_status(self):
            return 0

        def read(self):
            return b"ok"

    class DummyStderr(DummyStdout):
        def read(self):
            return b""

    class DummyClient:
        def exec_command(self, command, timeout=None):
            self.timeout = timeout
            return None, DummyStdout(), DummyStderr()

    ssh.client = DummyClient()
    code, out, err = ssh.execute("echo ok")
    assert code == 0
    assert out == "ok"
    assert ssh.client.timeout == 9


def test_ssh_execute_timeout_returns_error(monkeypatch):
    ssh = SSHConnection(host="localhost", port=22, username="user", command_timeout=3)

    class DummyClient:
        def exec_command(self, command, timeout=None):
            raise socket.timeout("timed out")

    ssh.client = DummyClient()
    code, out, err = ssh.execute("sleep 10")
    assert code == -1
    assert "timed out" in err.lower()
