"""Service scaffolding and deployment for Docker-based FastAPI projects."""

import json
import shlex
import textwrap
from pathlib import Path
from typing import Optional, Sequence

from rich.console import Console

from .ssh import SSHConnection
from .ingress import INGRESS_NETWORK, normalize_ingress_networks
from .paths import SERVICES_DIR

console = Console()

# Heuristics for auto-detecting FastAPI app entrypoints
_FASTAPI_ENTRYPOINT_CANDIDATES = [
    ("main.py", "app", 8000),
    ("app/main.py", "app.main:app", 8000),
    ("src/main.py", "src.main:app", 8000),
    ("api/main.py", "api.main:app", 8000),
]
_FASTAPI_IMPORTS = ["from fastapi", "import fastapi", "FastAPI("]


def detect_fastapi_entrypoint(project_dir: Path) -> tuple[str, str, int]:
    """Heuristically detect the FastAPI uvicorn entrypoint and default port.

    Returns:
        (file_rel_path, uvicorn_app_string, port)
    """
    for rel_path, app_str, port in _FASTAPI_ENTRYPOINT_CANDIDATES:
        full = project_dir / rel_path
        if full.exists():
            text = full.read_text(errors="ignore")
            if any(marker in text for marker in _FASTAPI_IMPORTS):
                return rel_path, app_str, port
    # Generic fallback
    return "main.py", "main:app", 8000


def render_dockerfile(app_str: str, port: int) -> str:
    """Return a minimal FastAPI Dockerfile."""
    return textwrap.dedent(f"""\
        FROM python:3.12-slim

        WORKDIR /app

        COPY requirements.txt ./
        RUN pip install --no-cache-dir -r requirements.txt

        COPY . .

        EXPOSE {port}

        CMD ["uvicorn", "{app_str}", "--host", "0.0.0.0", "--port", "{port}"]
    """)


def render_service_compose(
    service_name: str,
    domain: str,
    port: int,
    image: Optional[str] = None,
    build: bool = True,
    ingress_network: Optional[str] = None,
    ingress_networks: Optional[Sequence[str]] = None,
    exposure_scope: str = "single",
) -> str:
    """Return a docker-compose.yml for a single FastAPI service.

    Uses caddy-docker-proxy labels for automatic ingress routing.
    The image is built locally by default; pass image= to use a pre-built image
    instead of a build directive.
    """
    source_line = f"    image: {image}" if image else "    build: ."
    resolved_networks = normalize_ingress_networks(
        ingress_networks or ([ingress_network] if ingress_network else None)
    )

    lines = [
        'version: "3.8"',
        "",
        "services:",
        f"  {service_name}:",
        source_line,
        f"    container_name: {service_name}",
        "    expose:",
        f'      - "{port}"',
        "    networks:",
    ]
    lines.extend(f"      - {network}" for network in resolved_networks)
    lines.extend([
        "    labels:",
        f"      caddy: {domain}",
        f'      caddy.reverse_proxy: "{{{{upstreams {port}}}}}"',
        f"      deploy.scope: {exposure_scope}",
        "    restart: unless-stopped",
        "",
        "networks:",
    ])
    for network in resolved_networks:
        lines.extend([
            f"  {network}:",
            "    external: true",
            f"    name: {network}",
        ])
    return "\n".join(lines) + "\n"


def render_service_metadata(
    service_name: str,
    domain: str,
    port: int,
    image: Optional[str] = None,
    ingress_networks: Optional[Sequence[str]] = None,
    exposure_scope: str = "single",
) -> str:
    """Render persisted metadata for a deployed service."""
    payload = {
        "service_name": service_name,
        "domain": domain,
        "port": port,
        "image": image,
        "ingress_networks": normalize_ingress_networks(ingress_networks),
        "exposure_scope": exposure_scope,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


class ServiceManager:
    """Manages remote deployment lifecycle for a single service."""

    def __init__(self, ssh: SSHConnection, remote_base: str = SERVICES_DIR):
        self.ssh = ssh
        self.remote_base = remote_base

    @staticmethod
    def _q(value: str) -> str:
        return shlex.quote(value)

    def _service_dir(self, service_name: str) -> str:
        return f"{self.remote_base}/{service_name}"

    def _service_metadata_path(self, service_name: str) -> str:
        return f"{self._service_dir(service_name)}/.deploy-service.json"

    def image_exists_remote(self, image: str) -> bool:
        """Check whether a Docker image is available on the remote host."""
        exit_code, _, _ = self.ssh.execute(
            f"docker image inspect {self._q(image)} >/dev/null 2>&1"
        )
        return exit_code == 0

    def context_is_git_repo(self, context_path: str) -> bool:
        """Return True when the build context path is a git working directory."""
        exit_code, _, _ = self.ssh.execute(
            f"test -d {self._q(context_path)} && cd {self._q(context_path)} && git rev-parse --is-inside-work-tree >/dev/null 2>&1"
        )
        return exit_code == 0

    def get_context_revision(self, context_path: str) -> Optional[str]:
        """Return the current short revision for a git build context on target."""
        exit_code, stdout, _ = self.ssh.execute(
            f"cd {self._q(context_path)} && git rev-parse --short HEAD"
        )
        if exit_code != 0:
            return None
        return stdout.strip() or None

    def build_image_from_context(self, image: str, context_path: str) -> bool:
        """Build a Docker image from an existing context directory on target."""
        console.print(f"[blue]Building '{image}' on target from {context_path}...[/blue]")
        exit_code, _, stderr = self.ssh.execute(
            f"docker build -t {self._q(image)} {self._q(context_path)}"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Remote docker build failed: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Image '{image}' built on target[/green]")
        return True

    def ensure_service_dir(self, service_name: str) -> bool:
        """Create the remote service directory."""
        remote_dir = self._service_dir(service_name)
        exit_code, _, stderr = self.ssh.execute(f"mkdir -p {self._q(remote_dir)}")
        if exit_code != 0:
            console.print(f"[red]✗ Failed to create service directory: {stderr.strip()}[/red]")
            return False
        return True

    def upload_compose(self, service_name: str, compose_content: str) -> bool:
        """Upload a docker-compose.yml to the remote service directory."""
        remote_dir = self._service_dir(service_name)
        remote_file = f"{remote_dir}/docker-compose.yml"

        write_cmd = (
            f"cat > {self._q(remote_file)} << 'ENDOFCOMPOSE'\n"
            f"{compose_content}\n"
            "ENDOFCOMPOSE"
        )
        exit_code, _, stderr = self.ssh.execute(write_cmd)
        if exit_code != 0:
            console.print(f"[red]✗ Failed to upload compose file: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Compose file uploaded to {remote_file}[/green]")
        return True

    def upload_metadata(self, service_name: str, metadata_content: str) -> bool:
        """Upload persisted service metadata to the target host."""
        remote_file = self._service_metadata_path(service_name)
        write_cmd = (
            f"cat > {self._q(remote_file)} << 'ENDOFMETADATA'\n"
            f"{metadata_content}\n"
            "ENDOFMETADATA"
        )
        exit_code, _, stderr = self.ssh.execute(write_cmd)
        if exit_code != 0:
            console.print(f"[red]✗ Failed to upload service metadata: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Service metadata uploaded to {remote_file}[/green]")
        return True

    def read_service_metadata(self, service_name: str) -> Optional[dict]:
        """Return persisted metadata for a deployed service, if available."""
        metadata_path = self._service_metadata_path(service_name)
        exit_code, stdout, _ = self.ssh.execute(f"cat {self._q(metadata_path)} 2>/dev/null")
        if exit_code != 0 or not stdout.strip():
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            console.print(f"[yellow]⚠ Invalid service metadata for '{service_name}'[/yellow]")
            return None

    def compose_up(self, service_name: str) -> bool:
        """Start the service via docker compose."""
        remote_dir = self._service_dir(service_name)
        compose_file = f"{remote_dir}/docker-compose.yml"
        console.print(f"[blue]Starting service '{service_name}'...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"docker compose -f {self._q(compose_file)} -p {self._q(service_name)} up -d --pull never"
        )
        if exit_code != 0:
            console.print(f"[red]✗ docker compose up failed: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Service '{service_name}' is up[/green]")
        return True

    def compose_down(self, service_name: str) -> bool:
        """Stop and remove the remote service containers."""
        remote_dir = self._service_dir(service_name)
        compose_file = f"{remote_dir}/docker-compose.yml"
        exit_code, _, stderr = self.ssh.execute(
            f"docker compose -f {self._q(compose_file)} -p {self._q(service_name)} down"
        )
        if exit_code != 0:
            console.print(f"[red]✗ docker compose down failed: {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Service '{service_name}' stopped[/green]")
        return True

    def restart(self, service_name: str) -> bool:
        """Restart a running service container by name."""
        exit_code, _, stderr = self.ssh.execute(
            f"docker restart {self._q(service_name)} >/dev/null 2>&1"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to restart service '{service_name}': {stderr.strip()}[/red]")
            return False
        console.print(f"[green]✓ Service '{service_name}' restarted[/green]")
        return True

    def get_logs(self, service_name: str, lines: int = 80) -> str:
        """Return recent logs for the service container."""
        _, stdout, _ = self.ssh.execute(
            f"docker logs --tail {lines} {self._q(service_name)} 2>&1"
        )
        return stdout

    def list_services(self) -> list[str]:
        """List known service names from the remote service directory."""
        exit_code, stdout, _ = self.ssh.execute(
            f"find {self._q(self.remote_base)} -mindepth 1 -maxdepth 1 -type d -exec basename {{}} ';' 2>/dev/null"
        )
        if exit_code != 0:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def reconcile_global_services(self, ingress_networks: Optional[Sequence[str]] = None) -> bool:
        """Re-render and restart globally exposed services for the active ingress networks."""
        resolved_networks = normalize_ingress_networks(ingress_networks)
        for service_name in self.list_services():
            metadata = self.read_service_metadata(service_name)
            if not metadata or metadata.get("exposure_scope") != "global":
                continue

            compose_content = render_service_compose(
                service_name=metadata.get("service_name", service_name),
                domain=metadata["domain"],
                port=int(metadata["port"]),
                image=metadata.get("image"),
                ingress_networks=resolved_networks,
                exposure_scope="global",
            )
            metadata_content = render_service_metadata(
                service_name=metadata.get("service_name", service_name),
                domain=metadata["domain"],
                port=int(metadata["port"]),
                image=metadata.get("image"),
                ingress_networks=resolved_networks,
                exposure_scope="global",
            )

            if not self.upload_compose(service_name, compose_content):
                return False
            if not self.upload_metadata(service_name, metadata_content):
                return False
            if not self.compose_up(service_name):
                return False

        return True

    def get_status(self, service_name: str) -> Optional[str]:
        """Return the running state of the service container, or None."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {self._q(service_name)} 2>/dev/null"
        )
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return None

    def get_deployed_image(self, service_name: str) -> Optional[str]:
        """Return the container image currently used by the deployed service."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.Config.Image}}}}' {self._q(service_name)} 2>/dev/null"
        )
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return None

    def get_container_ip(self, service_name: str) -> Optional[str]:
        """Return the container's IP on the ingress network."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format "
            f"'{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' "
            f"{self._q(service_name)} 2>/dev/null"
        )
        if exit_code == 0 and stdout.strip():
            return stdout.strip()
        return None
