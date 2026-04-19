"""Service deploy workflow argument resolution and execution helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .config import DeployConfig
from .ingress import normalize_ingress_networks
from .paths import REPOS_DIR
from .push_flow import PushExecutionContext, execute_push
from .remote import RemoteServer
from .service import ServiceManager
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target


@dataclass(slots=True)
class ServiceDeployExecutionContext:
    """Fully resolved arguments required to execute deploy service deploy."""

    service_name: str
    sync: bool
    force: bool
    profile: ConnectionProfile


@dataclass(slots=True)
class ServiceDeployResolutionResult:
    """Resolved service-deploy execution context."""

    context: ServiceDeployExecutionContext


class ServiceDeployArgumentResolver:
    """Resolve service-deploy arguments from CLI input and config fallback."""

    def __init__(self, *, use_config: bool):
        self.use_config = use_config

    def resolve(
        self,
        config: DeployConfig,
        *,
        name: str | None,
        sync: bool,
        force: bool = False,
        profile: ConnectionProfile,
    ) -> ServiceDeployResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config, "svc.up", profile, use_config=self.use_config
        )
        if completed_profile is None:
            return None

        service_name = name or Path(".").resolve().name

        return ServiceDeployResolutionResult(
            context=ServiceDeployExecutionContext(
                service_name=service_name,
                sync=sync,
                force=force,
                profile=completed_profile,
            )
        )


def execute_service_deploy(
    context: ServiceDeployExecutionContext,
    console: Console,
) -> tuple[bool, Any | None]:
    """Execute deploy service deploy: start service using existing image on remote."""
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            svc_mgr = ServiceManager(ssh)
            service_name = context.service_name

            local_definition = _load_local_service_definition(service_name)
            if local_definition is None:
                console.print(
                    "[red]✗ docker-compose.yml is required. Run deploy service init first or provide a scaffolded compose file.[/red]"
                )
                return False, None

            domain = local_definition["domain"]
            internal = bool(local_definition["internal"])
            path_prefix = local_definition["path_prefix"]
            port = int(local_definition["port"])
            global_ingress = bool(local_definition["global_ingress"])
            ingress_networks = tuple(normalize_ingress_networks(local_definition["ingress_networks"]))

            # Internal services don't need a routable domain; use the service
            # name as a stable placeholder so metadata remains well-formed.
            if internal and not domain:
                domain = service_name
            if not domain and not internal:
                console.print(
                    "[red]✗ docker-compose.yml must include a caddy host label for non-internal services.[/red]"
                )
                return False, None

            image = local_definition["image"] or _default_service_image_name(service_name)

            console.print(Panel.fit(
                f"[bold blue]Service up — {service_name}[/bold blue]\n"
                f"Image: {image}  Domain: {domain}  Port: {port}\n"
                f"Remote: {display_target(ssh)}\n"
                + (f"Path prefix: {path_prefix}\n" if path_prefix else "")
                + (f"Mode: internal (no ingress)\n" if internal else f"Ingress: {'all configured networks' if global_ingress else ', '.join(ingress_networks)}"),
                border_style="blue",
            ))

            routed_host_getter = getattr(svc_mgr, "get_routed_host", None)
            current_routed_host = routed_host_getter(service_name) if callable(routed_host_getter) else None
            if current_routed_host:
                console.print(f"[dim]Current routed host: {current_routed_host}[/dim]")

            remote = RemoteServer(ssh, REPOS_DIR)
            bare_repo_path = remote.get_bare_repo_path(service_name)
            work_dir_path = remote.get_working_dir_path(service_name)

            if context.sync:
                console.print("\n[bold]Step 0: Sync repository[/bold]")
                push_context = PushExecutionContext(
                    repo_path=".",
                    deploy_path=REPOS_DIR,
                    profile=context.profile,
                    force=context.force,
                )
                if not execute_push(push_context, console):
                    console.print("[red]✗ Repository sync failed[/red]")
                    return False, None

                console.print("\n[bold]Step 1: Pull to remote work dir[/bold]")
                if not remote.clone_or_update_working_dir(bare_repo_path, work_dir_path, force=context.force):
                    console.print("[red]✗ Failed to pull working directory from bare repo[/red]")
                    return False, None

            if not remote.directory_exists(work_dir_path):
                console.print(
                    f"[red]✗ Remote work directory does not exist: {work_dir_path}[/red]"
                )
                console.print("[dim]Run: deploy svc up --sync to sync and deploy[/dim]")
                return False, None

            step_base = 2 if context.sync else 1
            console.print(f"\n[bold]Step {step_base}: Verify image on remote host[/bold]")
            if not svc_mgr.image_exists_remote(image):
                console.print(f"[red]✗ Image '{image}' not found on remote host[/red]")
                console.print(f"[dim]Use: deploy image push --image {image}[/dim]")
                console.print("[dim]Or: deploy image build --tag <image:tag>[/dim]")
                return False, None
            console.print(f"[green]✓ Image '{image}' found on remote host[/green]")

            console.print(f"\n[bold]Step {step_base + 1}: Read compose file from remote[/bold]")
            compose_content = svc_mgr.read_compose_from_remote(service_name)
            if not compose_content:
                console.print(
                    "[red]✗ docker-compose.yml not found in remote work directory[/red]"
                )
                console.print(
                    f"[dim]Expected at: {remote._q(remote.get_working_dir_path(service_name) + '/docker-compose.yml')}[/dim]"
                )
                return False, None
            console.print(f"[green]✓ Compose file read from remote work dir[/green]")

            console.print(f"\n[bold]Step {step_base + 2}: Start service[/bold]")
            if not svc_mgr.compose_up(service_name):
                return False, None

            console.print(f"\n[bold green]✓ Service '{service_name}' deployed[/bold green]")
            print_service_status_block(service_name, svc_mgr, console)

            return True, ssh
    except ConnectionError:
        return False, None


def print_service_status_block(
    service_name: str,
    svc_mgr: ServiceManager,
    console: Console,
) -> None:
    """Print the standardised svc status block to console.

    Output format (per REQUIREMENT.md):
        Route host: <caddy label on running container>
        Metadata domain: <persisted domain from .deploy-service.json>
        Ingress access: curl ...
        In-network access: http://<service>:<port>/
        [warning if route host != metadata domain]
        [recent container logs]
    """
    container_state = svc_mgr.get_status(service_name)
    if not container_state:
        console.print(f"[yellow]Service '{service_name}' not found on target[/yellow]")
        return

    colour = "green" if container_state == "running" else "yellow"
    console.print(f"[{colour}]Container state: {container_state}[/{colour}]")

    route_host = svc_mgr.get_routed_host(service_name)
    # Only use docker-compose.yml/config for metadata
    if not container_state:
        console.print(f"[yellow]Service '{service_name}' not found on target[/yellow]")
        return
    colour = "green" if container_state == "running" else "yellow"
    console.print(f"[{colour}]Container state: {container_state}[/{colour}]")
    route_host = svc_mgr.get_routed_host(service_name)
    if route_host:
        console.print(f"Route host: {route_host}")
    else:
        console.print("[dim]Route host: (none — internal service or container not running)[/dim]")
    # [recent container logs]
    logs = svc_mgr.get_logs(service_name, lines=20)
    if logs and logs.strip():
        console.print("\n[bold]Recent logs:[/bold]")
        console.print(logs.rstrip())


def persist_service_deploy_resolution(config: DeployConfig, connection: Any) -> dict[str, Any]:
    """Save resolved service-deploy connection args for later runs."""
    args_to_save = connection_args_from_connection(connection)
    config.save_args(args_to_save, "svc.up")
    return args_to_save


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_local_service_definition(service_name: str) -> dict[str, Any] | None:
    """Load scaffolded service intent from local docker-compose.yml."""
    compose_path = Path("docker-compose.yml")
    if not compose_path.exists():
        return None
    try:
        compose_text = compose_path.read_text()
    except Exception:
        return None

    lines = compose_text.splitlines()
    service_block = _extract_service_block(lines, service_name)
    if service_block is None:
        return None

    image = _extract_image(service_block)
    port = _extract_port(service_block)
    domain = _extract_caddy_domain(service_block)
    path_prefix = _extract_path_prefix(service_block)
    ingress_networks = _extract_networks(service_block)
    exposure_scope = _extract_deploy_scope(service_block)
    internal = exposure_scope == "internal" or (domain is None and not ingress_networks)

    return {
        "image": image,
        "port": port,
        "domain": domain,
        "path_prefix": path_prefix,
        "ingress_networks": ingress_networks,
        "global_ingress": exposure_scope == "global",
        "internal": internal,
    }


def _default_service_image_name(service_name: str) -> str:
    """Return a predictable Docker image name for service-side builds."""
    normalized = re.sub(r"[^a-z0-9._/-]", "-", service_name.lower())
    normalized = normalized.strip("-./")
    if not normalized:
        normalized = "service"
    return f"{normalized}:latest"


def _extract_service_block(lines: list[str], service_name: str) -> list[str] | None:
    """Return a service block by name, falling back to the first service entry."""
    in_services = False
    block_start = None
    fallback_start = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "services:":
            in_services = True
            continue
        if not in_services:
            continue

        if line and not line.startswith(" ") and stripped:
            break

        match = re.match(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", line)
        if not match:
            continue

        if fallback_start is None:
            fallback_start = idx
        if match.group(1) == service_name:
            block_start = idx
            break

    if block_start is None:
        block_start = fallback_start
    if block_start is None:
        return None

    block_end = len(lines)
    for idx in range(block_start + 1, len(lines)):
        line = lines[idx]
        if re.match(r"^\s{2}[A-Za-z0-9_.-]+:\s*$", line):
            block_end = idx
            break
        if line and not line.startswith(" "):
            block_end = idx
            break

    return lines[block_start:block_end]


def _extract_image(service_block: list[str]) -> str | None:
    for line in service_block:
        match = re.match(r"^\s{4}image:\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip().strip("\"'")
    return None


def _extract_port(service_block: list[str]) -> int:
    in_expose = False
    for line in service_block:
        if re.match(r"^\s{4}expose:\s*$", line):
            in_expose = True
            continue
        if in_expose and re.match(r"^\s{6}-\s*", line):
            value = line.split("-", 1)[1].strip().strip("\"'")
            try:
                return int(value)
            except ValueError:
                return 8000
        if in_expose and re.match(r"^\s{4}[A-Za-z0-9_.-]+:\s*$", line):
            break
    return 8000


def _extract_caddy_domain(service_block: list[str]) -> str | None:
    for line in service_block:
        match = re.match(r"^\s{6}caddy:\s*(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip().strip("\"'")
        value = value.removeprefix("http://").removeprefix("https://")
        return value
    return None


def _extract_path_prefix(service_block: list[str]) -> str | None:
    for line in service_block:
        match = re.match(r"^\s{6}caddy\.handle_path:\s*(.+?)\s*$", line)
        if not match:
            continue
        raw = match.group(1).strip().strip("\"'")
        normalized = raw.rstrip("*").rstrip("/")
        if not normalized:
            return "/"
        return normalized
    return None


def _extract_networks(service_block: list[str]) -> tuple[str, ...]:
    in_networks = False
    networks: list[str] = []
    for line in service_block:
        if re.match(r"^\s{4}networks:\s*$", line):
            in_networks = True
            continue
        if in_networks and re.match(r"^\s{6}-\s*", line):
            value = line.split("-", 1)[1].strip().strip("\"'")
            if value:
                networks.append(value)
            continue
        if in_networks and re.match(r"^\s{4}[A-Za-z0-9_.-]+:\s*$", line):
            break
    return tuple(networks)


def _extract_deploy_scope(service_block: list[str]) -> str:
    for line in service_block:
        match = re.match(r"^\s{6}deploy\.scope:\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip().strip("\"'")
    return "single"

