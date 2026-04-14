"""Ingress proxy management using lucaslorentz/caddy-docker-proxy."""

import re
import shlex
from pathlib import Path
from typing import Optional

from rich.console import Console

from .ssh import SSHConnection
from .caddy import CaddyManager

console = Console()

PROXY_IMAGE = "lucaslorentz/caddy-docker-proxy:latest"
INGRESS_NETWORK = "ingress"
PROXY_CONTAINER = "caddy-proxy"
PROXY_COMPOSE_REMOTE = "/opt/caddy-proxy/docker-compose.yml"
PROXY_BOOTSTRAP_CADDYFILE_REMOTE = "/opt/caddy-proxy/Caddyfile"
PROXY_NATIVE_CADDYFILE_BACKUP_REMOTE = "/opt/caddy-proxy/Caddyfile.native.backup"
PROXY_HOST_GATEWAY_NAME = "host.docker.internal"
PROXY_AUTOSAVE_CADDYFILE_REMOTE = "/config/caddy/Caddyfile.autosave"
PROXY_COMPOSE_LOCAL_TEMPLATE = Path(__file__).parent / "templates" / "proxy_compose.yml"

# Caddy persistent storage on the remote host
CADDY_DATA_VOLUME = "caddy_data"
CADDY_CONFIG_VOLUME = "caddy_config"


class ProxyManager:
    """Manages the caddy-docker-proxy ingress container on a remote server."""

    def __init__(self, ssh: SSHConnection):
        self.ssh = ssh

    @staticmethod
    def _q(value: str) -> str:
        return shlex.quote(value)

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def network_exists(self, name: str = INGRESS_NETWORK) -> bool:
        """Check whether a Docker network exists on the remote host."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker network inspect {self._q(name)} >/dev/null 2>&1 && echo yes || echo no"
        )
        return stdout.strip() == "yes"

    def ensure_network(self, name: str = INGRESS_NETWORK) -> bool:
        """Create the shared ingress network if it does not exist."""
        if self.network_exists(name):
            console.print(f"[dim]Network '{name}' already exists[/dim]")
            return True
        console.print(f"[blue]Creating Docker network '{name}'...[/blue]")
        exit_code, _, stderr = self.ssh.execute(
            f"docker network create {self._q(name)}"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to create network '{name}': {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Created network '{name}'[/green]")
        return True

    # ------------------------------------------------------------------
    # Image availability
    # ------------------------------------------------------------------

    def proxy_image_exists_remote(self) -> bool:
        """Check whether the proxy image is available on the remote host."""
        exit_code, _, _ = self.ssh.execute(
            f"docker image inspect {self._q(PROXY_IMAGE)} >/dev/null 2>&1"
        )
        return exit_code == 0

    # ------------------------------------------------------------------
    # Compose file deployment
    # ------------------------------------------------------------------

    def _render_compose(self) -> str:
        """Return rendered ingress compose YAML."""
        return PROXY_COMPOSE_LOCAL_TEMPLATE.read_text()

    def deploy_compose_file(self) -> bool:
        """Upload the ingress proxy docker-compose.yml to the remote host."""
        compose_content = self._render_compose()
        remote_dir = str(Path(PROXY_COMPOSE_REMOTE).parent)

        # Create remote directory
        exit_code, _, stderr = self.ssh.execute(f"mkdir -p {self._q(remote_dir)}")
        if exit_code != 0:
            console.print(f"[red]✗ Failed to create proxy directory: {stderr.strip()}[/red]")
            return False

        # Write compose file via heredoc
        write_cmd = f"cat > {self._q(PROXY_COMPOSE_REMOTE)} << 'ENDOFCOMPOSE'\n{compose_content}\nENDOFCOMPOSE"
        exit_code, _, stderr = self.ssh.execute(write_cmd)
        if exit_code != 0:
            console.print(f"[red]✗ Failed to write compose file: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Compose file deployed to {PROXY_COMPOSE_REMOTE}[/green]")
        return True

    def write_bootstrap_caddyfile(self, caddyfile_content: str) -> bool:
        """Write the bootstrap Caddyfile used by caddy-docker-proxy.

        The bootstrap file lets us preserve existing native Caddy routes while
        docker label-driven routes are discovered.
        """
        remote_dir = str(Path(PROXY_BOOTSTRAP_CADDYFILE_REMOTE).parent)
        exit_code, _, stderr = self.ssh.execute(f"mkdir -p {self._q(remote_dir)}")
        if exit_code != 0:
            console.print(f"[red]✗ Failed to create proxy directory: {stderr.strip()}[/red]")
            return False

        write_cmd = (
            f"cat > {self._q(PROXY_BOOTSTRAP_CADDYFILE_REMOTE)} << 'ENDOFCADDYFILE'\n"
            f"{caddyfile_content}\n"
            "ENDOFCADDYFILE"
        )
        exit_code, _, stderr = self.ssh.execute(write_cmd)
        if exit_code != 0:
            console.print(f"[red]✗ Failed to write bootstrap Caddyfile: {stderr.strip()}[/red]")
            return False
        console.print(
            f"[green]✓ Bootstrap Caddyfile written to {PROXY_BOOTSTRAP_CADDYFILE_REMOTE}[/green]"
        )
        return True

    # ------------------------------------------------------------------
    # Native Caddy migration helpers
    # ------------------------------------------------------------------

    def native_caddy_exists(self) -> bool:
        """Return True if native Caddy appears to be installed/running on host."""
        checks = [
            "systemctl list-unit-files caddy.service >/dev/null 2>&1",
            "command -v caddy >/dev/null 2>&1",
            "pgrep -x caddy >/dev/null 2>&1",
        ]
        for check in checks:
            exit_code, _, _ = self.ssh.execute(check)
            if exit_code == 0:
                return True
        return False

    def read_native_caddyfile(self) -> Optional[str]:
        """Read native Caddy config using the same discovery logic as CaddyManager."""
        config = CaddyManager(self.ssh).read_caddy_config()
        if config and config.strip():
            return config
        return None

    def get_native_caddyfile_path(self) -> str:
        """Return the detected native Caddy config path."""
        return CaddyManager(self.ssh).get_caddy_config_path()

    def backup_native_caddyfile(self, caddyfile_content: str) -> bool:
        """Store a copy of the native Caddy config under the proxy working dir."""
        remote_dir = str(Path(PROXY_NATIVE_CADDYFILE_BACKUP_REMOTE).parent)
        exit_code, _, stderr = self.ssh.execute(f"mkdir -p {self._q(remote_dir)}")
        if exit_code != 0:
            console.print(f"[red]✗ Failed to create proxy directory: {stderr.strip()}[/red]")
            return False

        write_cmd = (
            f"cat > {self._q(PROXY_NATIVE_CADDYFILE_BACKUP_REMOTE)} << 'ENDNATIVECADDYFILE'\n"
            f"{caddyfile_content}\n"
            "ENDNATIVECADDYFILE"
        )
        exit_code, _, stderr = self.ssh.execute(write_cmd)
        if exit_code != 0:
            console.print(f"[red]✗ Failed to back up native Caddyfile: {stderr.strip()}[/red]")
            return False
        console.print(
            f"[green]✓ Native Caddyfile backed up to {PROXY_NATIVE_CADDYFILE_BACKUP_REMOTE}[/green]"
        )
        return True

    def rewrite_native_caddyfile_for_container(self, caddyfile_content: str) -> str:
        """Rewrite host-local upstreams so they still work inside a container.

        Native Caddy often proxies to localhost/127.0.0.1 on the host. Once the
        config runs inside docker-caddy-proxy, those addresses resolve to the
        proxy container itself. We translate them to the Docker host gateway.
        """
        patterns = [
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>localhost)(?P<suffix>:\d+)",
             rf"\g<prefix>{PROXY_HOST_GATEWAY_NAME}\g<suffix>"),
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>127\.0\.0\.1)(?P<suffix>:\d+)",
             rf"\g<prefix>{PROXY_HOST_GATEWAY_NAME}\g<suffix>"),
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>\[::1\])(?P<suffix>:\d+)",
             rf"\g<prefix>{PROXY_HOST_GATEWAY_NAME}\g<suffix>"),
        ]

        rewritten = caddyfile_content
        for pattern, replacement in patterns:
            rewritten = re.sub(pattern, replacement, rewritten)
        return rewritten

    def detect_ingress_gateway_ip(self, network_name: str = INGRESS_NETWORK) -> Optional[str]:
        """Return the Docker gateway IP for the given bridge network, if available."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker network inspect {self._q(network_name)} "
            f"--format '{{{{(index .IPAM.Config 0).Gateway}}}}' 2>/dev/null"
        )
        gateway = stdout.strip()
        if exit_code == 0 and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", gateway):
            return gateway
        return None

    def rewrite_native_caddyfile_for_bridge_mode(self, caddyfile_content: str) -> str:
        """Rewrite loopback upstreams to a host address reachable from bridge mode.

        Prefer the ingress bridge gateway (host side of bridge). Fall back to
        host.docker.internal when gateway discovery is unavailable.
        """
        target_host = self.detect_ingress_gateway_ip() or PROXY_HOST_GATEWAY_NAME

        patterns = [
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>localhost)(?P<suffix>:\d+)",
             rf"\g<prefix>{target_host}\g<suffix>"),
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>127\.0\.0\.1)(?P<suffix>:\d+)",
             rf"\g<prefix>{target_host}\g<suffix>"),
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>127\.0\.1\.1)(?P<suffix>:\d+)",
             rf"\g<prefix>{target_host}\g<suffix>"),
            (r"(?P<prefix>reverse_proxy\s+(?:[^\n{]+?\s+)?)(?P<host>\[::1\])(?P<suffix>:\d+)",
             rf"\g<prefix>{target_host}\g<suffix>"),
        ]

        rewritten = caddyfile_content
        for pattern, replacement in patterns:
            rewritten = re.sub(pattern, replacement, rewritten)
        return rewritten

    def native_config_uses_loopback_upstreams(self, caddyfile_content: str) -> bool:
        """Return True if native config contains localhost/loopback reverse_proxy targets."""
        pattern = (
            r"reverse_proxy\s+(?:[^\n{]+\s+)?"
            r"(?:localhost|127\.0\.0\.1|127\.0\.1\.1|\[::1\]):\d+"
        )
        return re.search(pattern, caddyfile_content) is not None

    def stop_native_caddy(self) -> bool:
        """Stop native Caddy service/process if running."""
        # systemd-managed caddy
        self.ssh.execute("systemctl stop caddy >/dev/null 2>&1 || true")
        # Use systemd status check; pgrep would also match containerized caddy processes.
        exit_code, stdout, _ = self.ssh.execute("systemctl is-active caddy 2>/dev/null || true")
        if stdout.strip() == "active":
            console.print("[red]✗ Native Caddy service is still active[/red]")
            return False
        console.print("[green]✓ Native Caddy stopped[/green]")
        return True

    def start_native_caddy(self) -> bool:
        """Best-effort rollback helper to restart native Caddy."""
        exit_code, _, _ = self.ssh.execute("systemctl start caddy >/dev/null 2>&1")
        if exit_code == 0:
            return True
        return False

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check whether the proxy container is running."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Running}}}}' {self._q(PROXY_CONTAINER)} 2>/dev/null"
        )
        return exit_code == 0 and stdout.strip() == "true"

    def get_status(self) -> Optional[str]:
        """Return the container status string, or None if not found."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {self._q(PROXY_CONTAINER)} 2>/dev/null"
        )
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return None

    def up(self) -> bool:
        """Bring up the ingress proxy via docker compose."""
        console.print("[blue]Starting ingress proxy...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"docker compose -f {self._q(PROXY_COMPOSE_REMOTE)} up -d --pull never"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to start ingress proxy: {stderr.strip()}[/red]")
            return False
        console.print("[green]✓ Ingress proxy is up[/green]")
        return True

    def pull_and_up(self) -> bool:
        """Bring up the ingress proxy, pulling the image from remote registry if available."""
        console.print("[blue]Starting ingress proxy (pulling image if available)...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"docker compose -f {self._q(PROXY_COMPOSE_REMOTE)} up -d"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to start ingress proxy: {stderr.strip()}[/red]")
            return False
        console.print("[green]✓ Ingress proxy is up[/green]")
        return True

    def down(self) -> bool:
        """Stop and remove the ingress proxy container."""
        console.print("[blue]Stopping ingress proxy...[/blue]")
        exit_code, _, stderr = self.ssh.execute(
            f"docker compose -f {self._q(PROXY_COMPOSE_REMOTE)} down"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to stop ingress proxy: {stderr.strip()}[/red]")
            return False
        console.print("[green]✓ Ingress proxy stopped[/green]")
        return True

    def get_proxy_logs(self, lines: int = 30) -> str:
        """Return the last N lines of proxy logs."""
        _, stdout, _ = self.ssh.execute(
            f"docker logs --tail {lines} {self._q(PROXY_CONTAINER)} 2>&1"
        )
        return stdout

    def read_remote_file(self, path: str) -> Optional[str]:
        """Read a remote file and return its contents, or None if unavailable."""
        exit_code, stdout, _ = self.ssh.execute(f"cat {self._q(path)} 2>/dev/null")
        if exit_code == 0 and stdout:
            return stdout
        return None

    def get_bootstrap_caddyfile(self) -> Optional[str]:
        """Return the mounted bootstrap Caddyfile content."""
        return self.read_remote_file(PROXY_BOOTSTRAP_CADDYFILE_REMOTE)

    def get_native_caddyfile_backup(self) -> Optional[str]:
        """Return the saved backup of the native Caddy config."""
        return self.read_remote_file(PROXY_NATIVE_CADDYFILE_BACKUP_REMOTE)

    def get_generated_caddyfile(self) -> Optional[str]:
        """Return the autosaved generated Caddyfile from inside the proxy container."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker exec {self._q(PROXY_CONTAINER)} cat {self._q(PROXY_AUTOSAVE_CADDYFILE_REMOTE)} 2>/dev/null"
        )
        if exit_code == 0 and stdout:
            return stdout
        return None

    def get_native_caddy_status(self) -> str:
        """Return systemd status output for native Caddy."""
        _, stdout, _ = self.ssh.execute("systemctl status caddy --no-pager -l 2>&1")
        return stdout

    def get_native_caddy_journal(self, lines: int = 80) -> str:
        """Return recent journal output for native Caddy."""
        _, stdout, _ = self.ssh.execute(
            f"journalctl -u caddy -n {lines} --no-pager 2>&1"
        )
        return stdout
