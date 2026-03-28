import pytest
from deploy.remote import RemoteServer
from deploy.ssh import SSHConnection

class DummySSH:
    def __init__(self):
        self.commands = []
        self.username = "user"
        self.host = "host"
        self.port = 22
    def execute(self, command):
        self.commands.append(command)
        if "mkdir -p" in command:
            return 1, "", "error"
        return 0, "exists", ""

def test_create_directory_fail():
    ssh = DummySSH()
    remote = RemoteServer(ssh, "/deploy")
    assert not remote.create_directory("/fail")

def test_directory_exists_true():
    ssh = DummySSH()
    remote = RemoteServer(ssh, "/deploy")
    assert remote.directory_exists("/deploy")
