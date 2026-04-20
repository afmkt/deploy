"""Diagnostic tools for deployment connectivity issues."""

from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from .ssh import SSHConnection
from .proxy import PROXY_CONTAINER

console = Console()


@dataclass
class ServiceDiagnostic:
    name: str
    container_status: str
    has_caddy_labels: bool
    caddy_target: Optional[str]
    container_ip: Optional[str]
    reachable_from_proxy: bool
    error_message: Optional[str] = None


@dataclass
class ProxyDiagnostic:
    is_running: bool
    status: Optional[str]
    has_config: bool
    config_preview: Optional[str]


class Diagnostics:
    """Run diagnostics on deployment connectivity."""

    def __init__(self, ssh: SSHConnection):
        self.ssh = ssh

    @staticmethod
    def _q(value: str) -> str:
        return value.replace("'", "''")

    def check_proxy(self) -> ProxyDiagnostic:
        """Check if the proxy container is running and has configuration."""
        exit_code, status, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {self._q(PROXY_CONTAINER)} 2>/dev/null"
        )
        is_running = exit_code == 0 and status.strip() == "running"
        
        status_output = None
        if exit_code == 0:
            status_output = status.strip()
        
        exit_code, config, _ = self.ssh.execute(
            f"docker exec {self._q(PROXY_CONTAINER)} cat /config/caddy/Caddyfile.autosave 2>/dev/null | head -50"
        )
        has_config = exit_code == 0 and bool(config.strip())
        
        config_preview = None
        if has_config:
            config_lines = config.strip().split('\n')[:20]
            config_preview = '\n'.join(config_lines)
        
        return ProxyDiagnostic(
            is_running=is_running,
            status=status_output,
            has_config=has_config,
            config_preview=config_preview,
        )

    def list_services_with_caddy_labels(self) -> list[str]:
        """List all containers that have caddy labels."""
        exit_code, stdout, _ = self.ssh.execute(
            "docker ps --format '{{.Names}}' | xargs -I {} docker inspect --format '{{.Name}}:{{range $k, $v := .Config.Labels}}{{$k}}={{$v}}{{end}}' {} 2>/dev/null | grep 'caddy=' | cut -d: -f1 | sed 's/^\\///'"
        )
        if exit_code != 0:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def check_service_connectivity(self, service_name: str) -> ServiceDiagnostic:
        """Check if a service is reachable from the proxy container."""
        exit_code, status, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {self._q(service_name)} 2>/dev/null"
        )
        container_status = status.strip() if exit_code == 0 else "not found"
        
        if container_status != "running":
            return ServiceDiagnostic(
                name=service_name,
                container_status=container_status,
                has_caddy_labels=False,
                caddy_target=None,
                container_ip=None,
                reachable_from_proxy=False,
                error_message="Container not running",
            )
        
        exit_code, caddy_label, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.Config.Labels.caddy}}}}' {self._q(service_name)} 2>/dev/null"
        )
        has_caddy_labels = exit_code == 0 and bool(caddy_label.strip())
        
        caddy_target = None
        if has_caddy_labels:
            exit_code, reverse_proxy, _ = self.ssh.execute(
                f"docker inspect --format '{{{{.Config.Labels.caddy.reverse_proxy}}}}' {self._q(service_name)} 2>/dev/null"
            )
            if exit_code == 0:
                caddy_target = reverse_proxy.strip()
        
        exit_code, ip, _ = self.ssh.execute(
            f"docker inspect --format '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' {self._q(service_name)} 2>/dev/null"
        )
        container_ip = ip.strip() if exit_code == 0 else None
        
        reachable = False
        error_msg = None
        if has_caddy_labels:
            exit_code, _, _ = self.ssh.execute(
                f"docker logs {self._q(PROXY_CONTAINER)} 2>&1 | grep -q '{self._q(service_name)}' && echo found || echo notfound"
            )
            reachable = exit_code == 0
        
        return ServiceDiagnostic(
            name=service_name,
            container_status=container_status,
            has_caddy_labels=has_caddy_labels,
            caddy_target=caddy_target,
            container_ip=container_ip,
            reachable_from_proxy=reachable,
            error_message=error_msg,
        )

    def run_full_diagnostic(self) -> list[ServiceDiagnostic]:
        """Run full diagnostic on all services with caddy labels."""
        services = self.list_services_with_caddy_labels()
        results = []
        for svc in services:
            results.append(self.check_service_connectivity(svc))
        return results

    def fix_service_connectivity(self, service_name: str) -> bool:
        """Fix connectivity by restarting the proxy to pick up service labels."""
        console.print(f"[blue]Restarting proxy to pick up labels for '{service_name}'...[/blue]")
        exit_code, _, stderr = self.ssh.execute(
            f"docker restart {self._q(PROXY_CONTAINER)}"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to restart proxy: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Proxy restarted. Waiting for label detection...[/green]")
        return True