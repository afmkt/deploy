import pytest
from deploy.ssh import SSHConnection


def test_ssh_connection_init():
    ssh = SSHConnection(host="localhost", port=22, username="user", password="pass")
    assert ssh.host == "localhost"
    assert ssh.port == 22
    assert ssh.username == "user"
    assert ssh.password == "pass"
    assert ssh.key_filename is None


def test_ssh_connection_keyfile():
    ssh = SSHConnection(host="localhost", port=22, username="user", key_filename="/path/to/key")
    assert ssh.key_filename == "/path/to/key"
