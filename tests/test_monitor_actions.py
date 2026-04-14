"""Tests for deploy.monitor.actions.ActionRunner."""

from deploy.monitor.actions import ActionRunner


class FakeSSH:
    def __init__(self, connect_ok=True):
        self._connect_ok = connect_ok
        self.disconnected = False

    def connect(self):
        return self._connect_ok

    def disconnect(self):
        self.disconnected = True


class FakeProxyManager:
    def __init__(self, ssh):
        self.ssh = ssh

    def up(self):
        return True

    def down(self):
        return True

    def ensure_network(self, name):
        return name == "ingress"

    def get_proxy_logs(self, lines=120):
        return "proxy logs"


class FakeServiceManager:
    def __init__(self, ssh):
        self.ssh = ssh

    def compose_up(self, name):
        return name == "api"

    def compose_down(self, name):
        return name == "api"

    def restart(self, name):
        return name == "api"

    def get_logs(self, name, lines=120):
        return f"logs {name}"


def test_action_runner_connect_failure():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(connect_ok=False),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("proxy_up")
    assert result.ok is False
    assert "failed" in result.message.lower()


def test_action_runner_proxy_up():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("proxy_up")
    assert result.ok is True


def test_action_runner_service_restart():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("service_restart", target="api")
    assert result.ok is True
    assert "restarted" in result.message


def test_action_runner_network_create_failure():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("network_create", value="missing")
    assert result.ok is False


def test_action_runner_service_action_requires_target():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("service_down", target="")
    assert result.ok is False
    assert "required" in result.message.lower()


def test_action_runner_network_create_requires_name():
    runner = ActionRunner(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    result = runner.run("network_create", value="")
    assert result.ok is False
    assert "required" in result.message.lower()
