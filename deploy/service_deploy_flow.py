"""Service deploy workflow argument resolution and execution helpers."""

from __future__ import annotations

import json
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
    image: str | None
    domain: str | None
    port: int
    deploy_path: str | None
    use_config: bool
    rebuild: bool
    allow_remote_domain_fallback: bool
    missing_image_action: str
    auto_sync_context: bool
    ingress_networks: tuple[str, ...]
    global_ingress: bool
    path_prefix: str | None
    internal: bool
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
        image: str | None,
        domain: str | None,
        port: int,
        deploy_path: str | None,
        rebuild: bool,
        allow_remote_domain_fallback: bool,
        missing_image_action: str,
        auto_sync_context: bool,
        ingress_networks: tuple[str, ...],
        global_ingress: bool,
        path_prefix: str | None,
        internal: bool,
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
                image=image,
                domain=domain,
                port=port,
                deploy_path=deploy_path,
                use_config=self.use_config,
                rebuild=rebuild,
                allow_remote_domain_fallback=allow_remote_domain_fallback,
                missing_image_action=missing_image_action,
                auto_sync_context=auto_sync_context,
                ingress_networks=tuple(normalize_ingress_networks(ingress_networks)),
                global_ingress=global_ingress,
                path_prefix=path_prefix,
                internal=internal,
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

            domain = context.domain
            service_name = context.service_name
            image = context.image

            # Internal services don't need a routable domain; use the service
            # name as a stable placeholder so metadata remains well-formed.
            if context.internal and not domain:
                domain = service_name

            domain, domain_source = _resolve_service_domain_with_source(domain, service_name, svc_mgr)
            if not domain:
                if context.internal:
                    domain = service_name
                elif not context.interactive:
                    console.print(
                        "[red]✗ Domain is required in non-interactive mode. Provide --domain or save domain in metadata.[/red]"
                    )
                    return False, None
                else:
                    from rich.prompt import Prompt

                    domain = Prompt.ask("Public domain / hostname")
                    if not domain:
                        console.print("[red]✗ Domain is required[/red]")
                        return False, None

            console.print(Panel.fit(
                f"[bold blue]Service deploy — {service_name}[/bold blue]\n"
                f"Image: {image or '<auto-resolve>'}  Domain: {domain}  Port: {context.port}\n"
                f"Target: {display_target(ssh)}\n"
                + (f"Path prefix: {context.path_prefix}\n" if context.path_prefix else "")
                + (f"Mode: internal (no ingress)\n" if context.internal else f"Ingress: {'all configured networks' if context.global_ingress else ', '.join(context.ingress_networks)}"),
                border_style="blue",
            ))

            routed_host_getter = getattr(svc_mgr, "get_routed_host", None)
            current_routed_host = routed_host_getter(service_name) if callable(routed_host_getter) else None
            if current_routed_host:
                console.print(f"[dim]Current routed host: {current_routed_host}[/dim]")
            if domain_source == "remote-metadata":
                console.print(
                    "[yellow]⚠ Domain resolved from persisted target metadata. "
                    "Use --domain to override stale routing.[/yellow]"
                )
                console.print(
                    "[dim]Example: deploy service deploy --name "
                    f"{service_name} --domain localhost[/dim]"
                )
                if not context.allow_remote_domain_fallback:
                    if not context.interactive:
                        console.print(
                            "[red]✗ Refusing remote metadata domain fallback in non-interactive mode. "
                            "Provide --domain or pass --allow-remote-domain-fallback.[/red]"
                        )
                        return False, None

                    from rich.prompt import Confirm

                    if not Confirm.ask(
                        "Proceed using persisted target metadata domain?",
                        default=False,
                    ):
                        console.print(
                            "[yellow]Aborted. Provide --domain to set routing explicitly.[/yellow]"
                        )
                        return False, None

            # Step 1: check ingress proxy is running
            console.print("\n[bold]Step 1: Check ingress proxy[/bold]")
            if not proxy_mgr.is_running():
                console.print("[yellow]⚠ Ingress proxy is not running[/yellow]")
                console.print("[dim]Run: deploy proxy up[/dim]")
                return False, None

            # Step 2: check image availability on remote
            console.print("\n[bold]Step 2: Check image on target[/bold]")
            resolved_image = _resolve_service_image(image, service_name, svc_mgr)
            if not resolved_image:
                if not context.interactive:
                    console.print(
                        "[red]✗ Image name is required in non-interactive mode. Provide --image or save image in metadata.[/red]"
                    )
                    return False, None
                from rich.prompt import Prompt

                resolved_image = Prompt.ask(
                    "Docker image name for target build/use",
                    default=_default_service_image_name(service_name),
                )
                if not resolved_image:
                    console.print("[red]✗ Image name is required[/red]")
                    return False, None

            image = resolved_image
            image_missing = not svc_mgr.image_exists_remote(image)
            if image_missing or context.rebuild:
                if context.rebuild and not image_missing:
                    console.print(f"[blue]Rebuilding image '{image}' on target...[/blue]")
                    choice = "build"
                else:
                    console.print(f"[yellow]Image '{image}' not found on target.[/yellow]")
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
                            f"[yellow]Build context not found on target at {context_path}.[/yellow]"
                        )
                        should_sync = context.auto_sync_context
                        if not should_sync and context.interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to target now using deploy push?",
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
                            f"[yellow]Revision mismatch: local {local_revision} vs target {remote_revision}[/yellow]"
                        )
                        should_sync = context.auto_sync_context
                        if not should_sync and context.interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to target now using deploy push?",
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
                console.print(f"[green]✓ Image '{image}' found on target[/green]")

            effective_networks = (
                proxy_mgr.get_configured_ingress_networks() if context.global_ingress else context.ingress_networks
            )

            # Step 3: ensure service directory and compose file
            console.print("\n[bold]Step 3: Upload service compose[/bold]")
            if not svc_mgr.ensure_service_dir(service_name):
                return False, None
            compose_content = render_service_compose(
                service_name=service_name,
                domain=domain,
                port=context.port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if context.global_ingress else "single",
                path_prefix=context.path_prefix,
                internal=context.internal,
            )
            if not svc_mgr.upload_compose(service_name, compose_content):
                return False, None
            metadata_content = render_service_metadata(
                service_name=service_name,
                domain=domain,
                port=context.port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if context.global_ingress else "single",
                path_prefix=context.path_prefix,
                internal=context.internal,
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
            if context.path_prefix:
                console.print(f"  Path   : {context.path_prefix}")
            console.print(f"  Status : {status}")
            console.print(f"  Exposure: {'internal' if context.internal else 'global' if context.global_ingress else 'single-network'}")
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

def _load_local_service_metadata() -> dict:
    """Load local service metadata from the current working directory."""
    local_metadata_path = Path(".deploy-service.json")
    if not local_metadata_path.exists():
        return {}
    try:
        loaded = json.loads(local_metadata_path.read_text())
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _resolve_service_metadata_field(field: str, service_name: str, svc_mgr: ServiceManager) -> str | None:
    """Resolve a service metadata field from local metadata first, then target metadata."""
    local_metadata = _load_local_service_metadata()
    local_value = local_metadata.get(field)
    if isinstance(local_value, str) and local_value.strip():
        return local_value

    remote_metadata = svc_mgr.read_service_metadata(service_name)
    if not isinstance(remote_metadata, dict):
        return None
    remote_value = remote_metadata.get(field)
    if isinstance(remote_value, str) and remote_value.strip():
        return remote_value
    return None


def _default_service_image_name(service_name: str) -> str:
    """Return a predictable Docker image name for service-side builds."""
    normalized = re.sub(r"[^a-z0-9._/-]", "-", service_name.lower())
    normalized = normalized.strip("-./")
    if not normalized:
        normalized = "service"
    return f"{normalized}:latest"


def _resolve_service_domain_with_source(
    domain: str | None,
    service_name: str,
    svc_mgr: ServiceManager,
) -> tuple[str | None, str | None]:
    """Resolve service domain and return both value and source label."""
    if domain:
        return domain, "cli"

    local_metadata = _load_local_service_metadata()
    local_value = local_metadata.get("domain")
    if isinstance(local_value, str) and local_value.strip():
        return local_value.strip(), "local-metadata"

    remote_metadata = svc_mgr.read_service_metadata(service_name)
    if isinstance(remote_metadata, dict):
        remote_value = remote_metadata.get("domain")
        if isinstance(remote_value, str) and remote_value.strip():
            return remote_value.strip(), "remote-metadata"
    return None, None


def _resolve_service_image(image: str | None, service_name: str, svc_mgr: ServiceManager) -> str | None:
    """Resolve service image from CLI, metadata, or deployed service state."""
    if image:
        return image

    resolved_image = _resolve_service_metadata_field("image", service_name, svc_mgr)
    if resolved_image:
        return resolved_image

    deployed_image_getter = getattr(svc_mgr, "get_deployed_image", None)
    if callable(deployed_image_getter):
        deployed_image = deployed_image_getter(service_name)
        if deployed_image:
            return deployed_image

    return _default_service_image_name(service_name)


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
