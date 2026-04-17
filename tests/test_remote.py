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
        # Simulate directory exists
        if "test -d" in command:
            if "exists" in command:
                return 0, "exists", ""
            return 0, "exists", ""
        # Simulate bare repo check
        if "test -f" in command:
            return 0, "is_bare", ""
        # Simulate git init --bare
        if "git init --bare" in command:
            return 0, "", ""
        # Simulate git clone
        if "git clone" in command:
            return 0, "", ""
        # Simulate git pull
        if "git pull" in command:
            return 0, "", ""
        # Simulate mkdir -p
        if "mkdir -p" in command:
            return 0, "", ""
        return 1, "", "error"

def test_remote_server_setup_deployment():
    ssh = DummySSH()
    remote = RemoteServer(ssh, "/deploy")
    success, url = remote.setup_deployment("repo")
    assert success
    assert url.startswith("ssh://")
    assert "/deploy/repo.git" in url

def test_remote_server_get_paths():
    ssh = DummySSH()
    remote = RemoteServer(ssh, "/deploy")
    assert remote.get_bare_repo_path("repo") == "/deploy/repo.git"
    assert remote.get_working_dir_path("repo") == "/deploy/repo.work"
