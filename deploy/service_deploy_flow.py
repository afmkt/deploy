"""Service deploy workflow argument resolution and execution helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .config import DeployConfig
from .git import GitRepository
from .ingress import normalize_ingress_networks
from .paths import REPOS_DIR
from .proxy import ProxyManager
from .remote import RemoteServer
from .service import ServiceManager, render_service_compose, render_service_metadata
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target, docker_push_args_for_connection, push_args_for_connection

DEFAULT_DEPLOY_PATH = REPOS_DIR


@dataclass(slots=True)
class ServiceDeployExecutionContext:
    """Fully resolved arguments required to execute deploy service deploy."""

    service_name: str
    deploy_path: str | None
    use_config: bool
    rebuild: bool
    missing_image_action: str
    auto_sync_context: bool
    profile: ConnectionProfile
    interactive: bool


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
        deploy_path: str | None,
        rebuild: bool,
        missing_image_action: str,
        auto_sync_context: bool,
        profile: ConnectionProfile,
        interactive: bool,
    ) -> ServiceDeployResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config, "service", profile, use_config=self.use_config
        )
        if completed_profile is None:
            return None

        service_name = name or Path(".").resolve().name

        return ServiceDeployResolutionResult(
            context=ServiceDeployExecutionContext(
                service_name=service_name,
                deploy_path=deploy_path,
                use_config=self.use_config,
                rebuild=rebuild,
                missing_image_action=missing_image_action,
                auto_sync_context=auto_sync_context,
                profile=completed_profile,
                interactive=interactive,
            )
        )


def execute_service_deploy(
    context: ServiceDeployExecutionContext,
    console: Console,
    *,
    config: DeployConfig,
    push_command: Any,
    docker_push_command: Any,
) -> tuple[bool, Any | None]:
    """Execute deploy service deploy using fully resolved arguments."""
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            svc_mgr = ServiceManager(ssh)
            proxy_mgr = ProxyManager(ssh)
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
                f"[bold blue]Service deploy — {service_name}[/bold blue]\n"
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

            # Step 1: check ingress proxy is running
            console.print("\n[bold]Step 1: Check ingress proxy[/bold]")
            if not proxy_mgr.is_running():
                console.print("[yellow]⚠ Ingress proxy is not running[/yellow]")
                console.print("[dim]Run: deploy proxy up[/dim]")
                return False, None

            # Step 2: check image availability on remote
            console.print("\n[bold]Step 2: Check image on remote host[/bold]")
            image_missing = not svc_mgr.image_exists_remote(image)
            if image_missing or context.rebuild:
                if context.rebuild and not image_missing:
                    console.print(f"[blue]Rebuilding image '{image}' on remote host...[/blue]")
                    choice = "build"
                else:
                    console.print(f"[yellow]Image '{image}' not found on remote host.[/yellow]")
                    choice = context.missing_image_action
                if choice == "ask":
                    if not context.interactive:
                        choice = "build"
                    else:
                        from rich.prompt import Prompt

                        choice = Prompt.ask(
                            "How would you like to provide the image?",
                            choices=["push", "build", "abort"],
                            default="build",
                        )
                if choice == "push":
                    from click.testing import CliRunner
                    runner = CliRunner()
                    result = runner.invoke(
                        docker_push_command,
                        docker_push_args_for_connection(image, ssh),
                        catch_exceptions=False,
                        standalone_mode=False,
                    )
                    if result.exit_code != 0:
                        console.print("[red]✗ docker-push failed[/red]")
                        return False, None
                    # Reconnect: docker-push opens its own target session
                    ssh.disconnect()
                    if not ssh.connect():
                        return False, None
                    svc_mgr = ServiceManager(ssh)
                    proxy_mgr = ProxyManager(ssh)
                elif choice == "build":
                    deploy_path = _resolve_service_deploy_path(
                        config, context.use_config, context.deploy_path, context.interactive
                    )
                    if not deploy_path:
                        console.print(
                            "[red]✗ Deploy path is required for remote build context in non-interactive mode. Provide --deploy-path or save push/pull deploy_path in config.[/red]"
                        )
                        return False, None
                    repo = GitRepository(".")
                    if not repo.validate():
                        console.print("[red]✗ Remote build requires a local git repository.[/red]")
                        return False, None
                    repo_name = repo.get_repo_name()
                    local_revision = repo.get_current_revision()
                    remote = RemoteServer(ssh, deploy_path)
                    context_path = remote.get_working_dir_path(repo_name)

                    if not svc_mgr.context_is_git_repo(context_path):
                        console.print(
                            f"[yellow]Build context not found on remote host at {context_path}.[/yellow]"
                        )
                        should_sync = context.auto_sync_context
                        if not should_sync and context.interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to remote host now using deploy push?",
                                choices=["yes", "no"],
                                default="yes",
                            ) == "yes"
                        if should_sync:
                            if not _sync_repo_context_and_reconnect(ssh, deploy_path, push_command, console):
                                return False, None
                            svc_mgr = ServiceManager(ssh)
                            remote = RemoteServer(ssh, deploy_path)
                            context_path = remote.get_working_dir_path(repo_name)
                        else:
                            console.print(
                                "[red]✗ Cannot build without synced remote context. Run deploy push first or enable --auto-sync-context.[/red]"
                            )
                            return False, None

                    remote_revision = svc_mgr.get_context_revision(context_path)
                    if not remote_revision:
                        console.print("[red]✗ Failed to read remote build context revision[/red]")
                        return False, None
                    if local_revision and remote_revision != local_revision:
                        console.print(
                            f"[yellow]Revision mismatch: local {local_revision} vs remote {remote_revision}[/yellow]"
                        )
                        should_sync = context.auto_sync_context
                        if not should_sync and context.interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to remote host now using deploy push?",
                                choices=["yes", "no"],
                                default="yes",
                            ) == "yes"
                        if should_sync:
                            if not _sync_repo_context_and_reconnect(ssh, deploy_path, push_command, console):
                                return False, None
                            svc_mgr = ServiceManager(ssh)
                            remote = RemoteServer(ssh, deploy_path)
                            context_path = remote.get_working_dir_path(repo_name)
                        else:
                            console.print(
                                "[red]✗ Remote context must match local revision before build. Run deploy push first or enable --auto-sync-context.[/red]"
                            )
                            return False, None

                    if not svc_mgr.context_is_git_repo(context_path):
                        console.print("[red]✗ Synced remote context is still unavailable[/red]")
                        return False, None
                    remote_revision = svc_mgr.get_context_revision(context_path)
                    if local_revision and remote_revision != local_revision:
                        console.print(
                            "[red]✗ Remote context revision still mismatches local repository after sync[/red]"
                        )
                        return False, None

                    if not svc_mgr.build_image_from_context(image, context_path):
                        return False, None
                else:
                    console.print(
                        f"[yellow]Run: deploy docker-push {' '.join(docker_push_args_for_connection(image, ssh))} first[/yellow]"
                    )
                    return False, None
            else:
                console.print(f"[green]✓ Image '{image}' found on remote host[/green]")

            effective_networks = (
                proxy_mgr.get_configured_ingress_networks() if global_ingress else ingress_networks
            )

            # Step 3: ensure service directory and compose file
            console.print("\n[bold]Step 3: Upload service compose[/bold]")
            if not svc_mgr.ensure_service_dir(service_name):
                return False, None
            compose_content = render_service_compose(
                service_name=service_name,
                domain=domain,
                port=port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if global_ingress else "single",
                path_prefix=path_prefix,
                internal=internal,
            )
            if not svc_mgr.upload_compose(service_name, compose_content):
                return False, None
            metadata_content = render_service_metadata(
                service_name=service_name,
                domain=domain,
                port=port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if global_ingress else "single",
                path_prefix=path_prefix,
                internal=internal,
            )
            if not svc_mgr.upload_metadata(service_name, metadata_content):
                return False, None

            # Step 4: start service
            console.print("\n[bold]Step 4: Start service[/bold]")
            if not svc_mgr.compose_up(service_name):
                return False, None

            status = svc_mgr.get_status(service_name)
            container_ip = svc_mgr.get_container_ip(service_name)

            console.print(f"\n[bold green]✓ Service '{service_name}' deployed[/bold green]")
            console.print(f"  Domain : {domain}")
            if path_prefix:
                console.print(f"  Path   : {path_prefix}")
            console.print(f"  Status : {status}")
            console.print(f"  Exposure: {'internal' if internal else 'global' if global_ingress else 'single-network'}")
            if container_ip:
                console.print(f"  Container IP: {container_ip}")

            return True, ssh
    except ConnectionError:
        return False, None


def persist_service_deploy_resolution(config: DeployConfig, connection: Any) -> dict[str, Any]:
    """Save resolved service-deploy connection args for later runs."""
    args_to_save = connection_args_from_connection(connection)
    config.save_args(args_to_save, "service")
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


def _resolve_service_deploy_path(
    config: DeployConfig,
    use_config: bool,
    deploy_path: str | None,
    interactive: bool,
) -> str | None:
    """Resolve the remote deploy path for repo-based build contexts."""
    if deploy_path:
        return deploy_path
    if use_config:
        for section in ("service", "push", "pull"):
            saved = config.load_args(section)
            if saved.get("deploy_path"):
                return saved["deploy_path"]
    if not interactive:
        return None
    from rich.prompt import Prompt

    return Prompt.ask(
        "Remote deploy path containing synced repository working directories",
        default=DEFAULT_DEPLOY_PATH,
    )


def _sync_repo_context_and_reconnect(ssh: Any, deploy_path: str, push_command: Any, console: Console) -> bool:
    """Run deploy push for the active target and reconnect the session."""
    from click.testing import CliRunner

    runner = CliRunner()
    push_result = runner.invoke(
        push_command,
        push_args_for_connection(".", deploy_path, ssh),
        catch_exceptions=False,
        standalone_mode=False,
    )
    if push_result.exit_code != 0:
        console.print("[red]✗ deploy push failed[/red]")
        return False
    ssh.disconnect()
    return ssh.connect()
