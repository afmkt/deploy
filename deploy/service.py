"""Service scaffolding and deployment for Docker-based FastAPI projects."""

import shlex
import textwrap
from pathlib import Path
from typing import Optional

from rich.console import Console

from .ssh import SSHConnection
from .proxy import INGRESS_NETWORK

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
    ingress_network: str = INGRESS_NETWORK,
) -> str:
    """Return a docker-compose.yml for a single FastAPI service.

    Uses caddy-docker-proxy labels for automatic ingress routing.
    The image is built locally by default; pass image= to use a pre-built image
    instead of a build directive.
    """
    source_line = f"    image: {image}" if image else "    build: ."

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
        "      - ingress_net",
        "    labels:",
        f"      caddy: {domain}",
        f'      caddy.reverse_proxy: "{{{{upstreams {port}}}}}"',
        "    restart: unless-stopped",
        "",
        "networks:",
        "  ingress_net:",
        "    external: true",
        f"    name: {ingress_network}",
    ]
    return "\n".join(lines) + "\n"


class ServiceManager:
    """Manages remote deployment lifecycle for a single service."""

    def __init__(self, ssh: SSHConnection, remote_base: str = "/opt/services"):
        self.ssh = ssh
        self.remote_base = remote_base

    @staticmethod
    def _q(value: str) -> str:
        return shlex.quote(value)

    def _service_dir(self, service_name: str) -> str:
        return f"{self.remote_base}/{service_name}"

    def image_exists_remote(self, image: str) -> bool:
        """Check whether a Docker image is available on the remote host."""
        exit_code, _, _ = self.ssh.execute(
            f"docker image inspect {self._q(image)} >/dev/null 2>&1"
        )
        return exit_code == 0

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

    def get_status(self, service_name: str) -> Optional[str]:
        """Return the running state of the service container, or None."""
        exit_code, stdout, _ = self.ssh.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {self._q(service_name)} 2>/dev/null"
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
