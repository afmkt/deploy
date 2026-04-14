"""Serialized action runner for monitor control operations."""

from __future__ import annotations

import threading

from deploy.proxy import ProxyManager
from deploy.service import ServiceManager
from deploy.ssh import SSHConnection

from .models import ActionResult


class ActionRunner:
    """Run mutating operations sequentially to avoid conflicting remote commands."""

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
        self._lock = threading.Lock()

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

    def run(self, action: str, target: str = "", value: str = "") -> ActionResult:
        """Run one action with optional target and value payloads."""
        if action in {"service_up", "service_down", "service_restart", "service_logs"} and not target.strip():
            return ActionResult(ok=False, action=action, message="Service name is required")

        if action == "network_create" and not value.strip():
            return ActionResult(ok=False, action=action, message="Network name is required")

        with self._lock:
            ssh = self._connect()
            if ssh is None:
                return ActionResult(ok=False, action=action, message="SSH connection failed")

            try:
                proxy_mgr = self.proxy_manager_factory(ssh)
                service_mgr = self.service_manager_factory(ssh)

                if action == "proxy_up":
                    ok = proxy_mgr.up()
                    return ActionResult(ok=ok, action=action, message="Proxy started" if ok else "Proxy start failed")

                if action == "proxy_down":
                    ok = proxy_mgr.down()
                    return ActionResult(ok=ok, action=action, message="Proxy stopped" if ok else "Proxy stop failed")

                if action == "service_up":
                    ok = service_mgr.compose_up(target)
                    return ActionResult(ok=ok, action=action, message=f"Service {target} started" if ok else f"Service {target} start failed")

                if action == "service_down":
                    ok = service_mgr.compose_down(target)
                    return ActionResult(ok=ok, action=action, message=f"Service {target} stopped" if ok else f"Service {target} stop failed")

                if action == "service_restart":
                    ok = service_mgr.restart(target)
                    return ActionResult(ok=ok, action=action, message=f"Service {target} restarted" if ok else f"Service {target} restart failed")

                if action == "network_create":
                    ok = proxy_mgr.ensure_network(value)
                    return ActionResult(ok=ok, action=action, message=f"Network {value} ready" if ok else f"Network {value} create failed")

                if action == "proxy_logs":
                    lines = int(value or "120")
                    logs = proxy_mgr.get_proxy_logs(lines=lines)
                    ok = bool(logs.strip())
                    return ActionResult(ok=ok, action=action, message=logs.strip() or "No proxy logs")

                if action == "service_logs":
                    lines = int(value or "120")
                    logs = service_mgr.get_logs(target, lines=lines)
                    ok = bool(logs.strip())
                    return ActionResult(ok=ok, action=action, message=logs.strip() or f"No logs for {target}")

                return ActionResult(ok=False, action=action, message=f"Unknown action: {action}")
            finally:
                ssh.disconnect()
