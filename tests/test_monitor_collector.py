"""Tests for deploy.monitor.collector.MonitorCollector."""

from deploy.monitor.collector import MonitorCollector


class FakeSSH:
    def __init__(self, connect_ok=True, command_map=None):
        self._connect_ok = connect_ok
        self.command_map = command_map or {}
        self.disconnected = False

    def connect(self):
        return self._connect_ok

    def execute(self, command):
        for pattern, result in self.command_map.items():
            if pattern in command:
                return result
        return (0, "", "")

    def disconnect(self):
        self.disconnected = True


class FakeProxyManager:
    def __init__(self, ssh):
        self.ssh = ssh

    def get_status(self):
        return "running"


class FakeServiceManager:
    def __init__(self, ssh):
        self.ssh = ssh

    def list_services(self):
        return ["api", "worker"]

    def get_status(self, name):
        return "running" if name == "api" else "exited"


def test_collect_connection_failure():
    collector = MonitorCollector(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: FakeSSH(connect_ok=False),
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    snapshot = collector.collect()
    assert snapshot.connected is False
    assert "failed" in snapshot.error.lower()


def test_collect_success():
    ssh = FakeSSH(
        connect_ok=True,
        command_map={
            "docker network ls": (0, "bridge\ningress\n", ""),
            "/proc/loadavg": (0, "0.10 0.20 0.30\n", ""),
            "free -m": (0, "128/1024MB (12%)\n", ""),
            "df -h /": (0, "2G/20G (10%)\n", ""),
            "docker ps -aq | wc -l": (0, "4\n", ""),
            "docker images -q | wc -l": (0, "7\n", ""),
        },
    )
    collector = MonitorCollector(
        host="example.com",
        port=22,
        username="root",
        key_filename=None,
        password=None,
        ssh_factory=lambda **_: ssh,
        proxy_manager_factory=FakeProxyManager,
        service_manager_factory=FakeServiceManager,
    )
    snapshot = collector.collect()
    assert snapshot.connected is True
    assert snapshot.proxy_status == "running"
    assert [svc.name for svc in snapshot.services] == ["api", "worker"]
    assert [svc.status for svc in snapshot.services] == ["running", "exited"]
    assert snapshot.networks == ["bridge", "ingress"]
    assert snapshot.resources.docker_containers == 4
    assert snapshot.resources.docker_images == 7
