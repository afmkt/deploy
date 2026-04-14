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


class StatusSSH:
    def __init__(self, status_output="", status_exit=0, log_output="", log_exit=0):
        self.commands = []
        self.username = "user"
        self.host = "host"
        self.port = 22
        self.status_output = status_output
        self.status_exit = status_exit
        self.log_output = log_output
        self.log_exit = log_exit

    def execute(self, command):
        self.commands.append(command)
        if "git status --porcelain" in command:
            return self.status_exit, self.status_output, ""
        if "git log \"origin/$branch\"..HEAD --oneline" in command:
            return self.log_exit, self.log_output, ""
        if "test -d" in command:
            return 0, "exists", ""
        if "git checkout" in command:
            return 0, "", ""
        if "git pull origin" in command:
            return 0, "", ""
        return 0, "", ""


def test_has_uncommitted_changes_true():
    ssh = StatusSSH(status_output=" M app.py\n")
    remote = RemoteServer(ssh, "/deploy")
    assert remote.has_uncommitted_changes("/deploy/repo") is True


def test_has_uncommitted_changes_error_returns_none():
    ssh = StatusSSH(status_exit=1)
    remote = RemoteServer(ssh, "/deploy")
    assert remote.has_uncommitted_changes("/deploy/repo") is None


def test_has_unpushed_commits_true():
    ssh = StatusSSH(log_output="abc123 test commit\n")
    remote = RemoteServer(ssh, "/deploy")
    assert remote.has_unpushed_commits("/deploy/repo") is True


def test_create_directory_quotes_path():
    ssh = StatusSSH()
    remote = RemoteServer(ssh, "/deploy")
    dangerous = "/tmp/repo path; touch /tmp/pwn"
    remote.create_directory(dangerous)
    assert ssh.commands
    assert "mkdir -p '/tmp/repo path; touch /tmp/pwn'" in ssh.commands[-1]


def test_clone_or_update_working_dir_blocks_dirty_worktree():
    ssh = StatusSSH(status_output=" M changed.txt\n")
    remote = RemoteServer(ssh, "/deploy")
    success = remote.clone_or_update_working_dir("/deploy/repo.git", "/deploy/repo", "main")
    assert success is False
