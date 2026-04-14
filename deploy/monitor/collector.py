"""Polling collector for monitor snapshots."""

from __future__ import annotations

from deploy.proxy import ProxyManager
from deploy.service import ServiceManager
from deploy.ssh import SSHConnection

from .models import ResourceState, ServiceState, Snapshot


class MonitorCollector:
    """Collect status data from a remote host over SSH."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        key_filename: str | None,
        password: str | None,
        command_timeout: float = 10.0,
        ssh_factory=SSHConnection,
        proxy_manager_factory=ProxyManager,
        service_manager_factory=ServiceManager,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.key_filename = key_filename
        self.password = password
        self.command_timeout = command_timeout
        self.ssh_factory = ssh_factory
        self.proxy_manager_factory = proxy_manager_factory
        self.service_manager_factory = service_manager_factory

    def _connect(self):
        ssh = self.ssh_factory(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            key_filename=self.key_filename,
            command_timeout=self.command_timeout,
        )
        if not ssh.connect():
            return None
        return ssh

    @staticmethod
    def _read_text(ssh: SSHConnection, command: str, default: str = "n/a") -> str:
        exit_code, stdout, _ = ssh.execute(command)
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return default

    @staticmethod
    def _read_int(ssh: SSHConnection, command: str, default: int = 0) -> int:
        value = MonitorCollector._read_text(ssh, command, default=str(default))
        try:
            return int(value)
        except ValueError:
            return default

    def collect(self) -> Snapshot:
        """Collect one full snapshot."""
        snapshot = Snapshot()
        ssh = self._connect()
        if ssh is None:
            snapshot.error = "SSH connection failed"
            return snapshot

        try:
            proxy_mgr = self.proxy_manager_factory(ssh)
            service_mgr = self.service_manager_factory(ssh)

            snapshot.connected = True
            snapshot.proxy_status = proxy_mgr.get_status() or "not-found"

            service_names = service_mgr.list_services()
            snapshot.services = [
                ServiceState(name=name, status=(service_mgr.get_status(name) or "not-found"))
                for name in sorted(service_names)
            ]

            networks_text = self._read_text(
                ssh,
                "docker network ls --format '{{.Name}}'",
                default="",
            )
            snapshot.networks = [line.strip() for line in networks_text.splitlines() if line.strip()]

            snapshot.resources = ResourceState(
                load_avg=self._read_text(
                    ssh,
                    "awk '{print $1 \" \" $2 \" \" $3}' /proc/loadavg",
                ),
                memory=self._read_text(
                    ssh,
                    "free -m 2>/dev/null | awk 'NR==2{printf \"%s/%sMB (%.0f%%)\", $3,$2,($3*100)/$2}'",
                ),
                disk=self._read_text(
                    ssh,
                    "df -h / | awk 'NR==2{print $3 \"/\" $2 \" (\" $5 \")\"}'",
                ),
                docker_containers=self._read_int(ssh, "docker ps -aq | wc -l"),
                docker_images=self._read_int(ssh, "docker images -q | wc -l"),
            )

            return snapshot
        finally:
            ssh.disconnect()
