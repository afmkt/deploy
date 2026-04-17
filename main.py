"""Git SSH Deploy Tool - Main CLI entry point."""

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deploy.git import GitRepository
from deploy.local import LocalConnection
from deploy.ssh import SSHConnection
from deploy.remote import RemoteServer
from deploy.push_flow import PushArgumentResolver, execute_push, persist_push_resolution
from deploy.pull_flow import PullArgumentResolver, execute_pull, persist_pull_resolution
from deploy.docker_push_flow import (
    DockerPushArgumentResolver,
    execute_docker_push,
    persist_docker_push_resolution,
)
from deploy.proxy_up_flow import (
    ProxyUpArgumentResolver,
    execute_proxy_up,
    persist_proxy_up_resolution,
)
from deploy.service_deploy_flow import (
    ServiceDeployArgumentResolver,
    execute_service_deploy,
    persist_service_deploy_resolution,
)
from deploy.session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from deploy.target import (
    display_target,
    docker_push_args_for_connection,
    is_local_connection,
    push_args_for_connection,
    proxy_healthcheck_url,
)
from deploy.proxy import ProxyManager
from deploy.ingress import INGRESS_NETWORK, normalize_ingress_networks
from deploy.paths import REPOS_DIR
from deploy.service import ServiceManager
from deploy.service_init_flow import ServiceInitArgumentResolver, execute_service_init
from deploy.config import DeployConfig
from deploy import __version__

console = Console()
DEFAULT_DEPLOY_PATH = REPOS_DIR


def _build_connection_from_config(
    config: DeployConfig,
    section: str,
    host: str,
    port: int,
    username: str,
    key: str,
    password: str,
    use_config: bool = True,
    command_timeout: float | None = None,
):
    """Return a remote connection, loading missing fields from saved config."""
    completed = resolve_connection_profile(
        config,
        section,
        ConnectionProfile(
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        use_config=use_config,
    )
    if completed is None:
        return None
    return build_connection(completed, command_timeout)


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default=DEFAULT_DEPLOY_PATH, help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without performing actual push")
def main(repo_path: str, host: str, port: int, username: str, key: str,
        password: str, deploy_path: str, interactive: bool, use_config: bool, dry_run: bool,
):
    """Git SSH Deploy Tool - Sync local Git repository to a remote repository.

    This tool automates repository setup, remote configuration, and deployment
    in a single command.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool[/bold blue]\n"
        "Sync local Git repository to remote server over SSH",
        border_style="blue"
    ))

    config = DeployConfig()
    resolver = PushArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=interactive,
        use_config=use_config,
    )
    resolution = resolver.resolve(
        config,
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=ConnectionProfile(
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
    )
    if resolution and resolution.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    console.print("\n[bold]Step 2: Configuring remote[/bold]")
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_push(resolution.context, console, dry_run=dry_run)
    if not success:
        sys.exit(1)

    persist_push_resolution(config, resolution.context)
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default=DEFAULT_DEPLOY_PATH, help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--commit/--no-commit", default=False, help="Commit changes in remote working directory")
@click.option("--sync-remote/--no-sync-remote", default=False, help="Check if remote working dir is clean, commit changes, push to bare repo, then pull")
@click.option("--branch", "-b", help="Branch name to pull to")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without performing actual pull")
def pull(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool, commit: bool,
        sync_remote: bool, branch: str, use_config: bool, dry_run: bool,
):
    """Pull from remote repository to local.

    This tool pulls changes from the remote repository to the local repository.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Pull Mode[/bold blue]\n"
        "Pull changes from remote repository to local",
        border_style="blue"
    ))

    config = DeployConfig()
    resolver = PullArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=interactive,
        use_config=use_config,
    )
    resolution = resolver.resolve(
        config,
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=ConnectionProfile(
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        commit=commit,
        sync_remote=sync_remote,
        branch=branch,
    )
    if resolution and resolution.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    console.print("\n[bold]Step 2: Configuring remote[/bold]")
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_pull(resolution.context, console, dry_run=dry_run)
    if not success:
        sys.exit(1)

    persist_pull_resolution(config, resolution.context)
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")


@click.command()
def show_config():
    """Show saved configuration."""
    config = DeployConfig()
    config_data = config.load_config()
    
    if not config_data:
        console.print("[yellow]No saved configuration found.[/yellow]")
        return
    
    console.print(Panel.fit(
        "[bold blue]Saved Configuration[/bold blue]",
        border_style="blue"
    ))
    
    for command, args in config_data.items():
        console.print(f"\n[bold]{command.upper()}[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Argument", style="cyan")
        table.add_column("Value", style="green")
        
        for key, value in args.items():
            table.add_row(key, str(value))
        
        console.print(table)
    
    console.print(f"\n[dim]Config file: {config.get_config_path()}[/dim]")


@click.command()
@click.option("--command", "-c", type=click.Choice(["push", "pull"]), help="Clear config for specific command only")
def clear_config(command: str):
    """Clear saved configuration."""
    config = DeployConfig()
    
    if command:
        config.clear_config(command)
        console.print(f"[green]✓ Cleared {command} configuration[/green]")
    else:
        config.clear_config()
        console.print("[green]✓ Cleared all configuration[/green]")

@click.command()
@click.option("--image", "-i", required=True, help="Docker image to push (name:tag)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--platform", help="Remote platform override (e.g. linux/amd64, linux/arm64)")
@click.option("--registry-username", help="Docker registry username for private images")
@click.option("--registry-password", help="Docker registry password for private images")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection without transferring image")
def docker_push(image: str, host: str, port: int, username: str, key: str,
                password: str, platform: str | None, registry_username: str,
                registry_password: str, interactive: bool, use_config: bool,
                                dry_run: bool):
    """Push a Docker image to the remote host.

    Pulls the image locally for the remote architecture, saves it to a tarball,
    copies it to the remote host, and loads it there.
    """
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Docker Push[/bold blue]\n"
        "Transfer a Docker image to the remote host",
        border_style="blue",
    ))

    config = DeployConfig()
    resolver = DockerPushArgumentResolver(interactive=interactive, use_config=use_config)
    resolution = resolver.resolve(
        config,
        image=image,
        profile=ConnectionProfile(
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        platform=platform,
        registry_username=registry_username,
        registry_password=registry_password,
    )
    if resolution and resolution.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")

    console.print("\n[bold]Step 1: Configuring remote[/bold]")
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_docker_push(resolution.context, console, dry_run=dry_run)
    if not success:
        sys.exit(1)

    persist_docker_push_resolution(config, resolution.context)
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")


# ---------------------------------------------------------------------------
# proxy subcommand group
# ---------------------------------------------------------------------------

@click.group()
def proxy():
    """Manage the caddy-docker-proxy ingress container on the remote host."""
    pass


@proxy.command(name="up")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--migrate-native-caddy/--no-migrate-native-caddy", default=True,
              help="If native Caddy exists, migrate its Caddyfile and stop it before proxy start")
@click.option("--ingress-network", "ingress_networks", multiple=True,
              help="Ingress networks for proxy/service discovery (repeat flag or use comma-separated values)")
@click.option("--interactive/--no-interactive", default=True,
              help="Interactive mode — disable for CI/CD pipelines")
def proxy_up(host, port, username, key, password, use_config, migrate_native_caddy, ingress_networks, interactive):
    """Start (or ensure running) the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    resolver = ProxyUpArgumentResolver(use_config=use_config)
    resolution = resolver.resolve(
        config,
        profile=ConnectionProfile(
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        ingress_networks=ingress_networks,
        migrate_native_caddy=migrate_native_caddy,
        interactive=interactive,
    )
    if resolution is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    success, active_connection = execute_proxy_up(resolution.context, console, docker_push)
    if not success or active_connection is None:
        sys.exit(1)

    persist_proxy_up_resolution(config, active_connection)


@proxy.command(name="status")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def proxy_status(host, port, username, key, password, use_config):
    """Show the status of the caddy-docker-proxy container."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)
            status = mgr.get_status()
            running = mgr.is_running()
            if status:
                if running:
                    console.print(f"[green]Ingress proxy is running ({status})[/green]")
                else:
                    console.print(f"[red]Ingress proxy is not running (status: {status})[/red]")
                    console.print("[dim]Run: deploy proxy up[/dim]")
                console.print(f"[dim]Health check: {proxy_healthcheck_url(ssh)}[/dim]")
            else:
                console.print("[yellow]Ingress proxy container not found[/yellow]")
                console.print("[dim]Run: deploy proxy up[/dim]")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="down")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def proxy_down(host, port, username, key, password, use_config):
    """Stop the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            ProxyManager(ssh).down()
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="logs")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--lines", default=80, show_default=True,
              help="How many proxy log lines to fetch")
def proxy_logs(host, port, username, key, password, use_config, lines):
    """Show recent docker-caddy-proxy container logs."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            logs = ProxyManager(ssh).get_proxy_logs(lines=lines)
            if logs.strip():
                console.print(logs.rstrip())
            else:
                console.print("[yellow]No proxy logs available[/yellow]")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="diagnose")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--lines", default=80, show_default=True,
              help="How many log/journal lines to fetch")
def proxy_diagnose(host, port, username, key, password, use_config, lines):
    """Collect proxy and native Caddy diagnostics from the remote host."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)

            console.print(Panel.fit(
                "[bold blue]Proxy Diagnose[/bold blue]\n"
                "Remote Caddy migration diagnostics",
                border_style="blue",
            ))

            sections = [
                ("Proxy Status", mgr.get_status() or "not found"),
                ("Health Endpoint", proxy_healthcheck_url(ssh)),
                ("Proxy Logs", mgr.get_proxy_logs(lines=lines).strip() or "<empty>"),
                (
                    "Generated Caddyfile",
                    (mgr.get_generated_caddyfile() or "<unavailable>").strip(),
                ),
                (
                    "Bootstrap Caddyfile",
                    (mgr.get_bootstrap_caddyfile() or "<unavailable>").strip(),
                ),
                (
                    "Native Caddy Status",
                    mgr.get_native_caddy_status().strip() or "<empty>",
                ),
                (
                    "Native Caddy Journal",
                    mgr.get_native_caddy_journal(lines=lines).strip() or "<empty>",
                ),
            ]

            for title, content in sections:
                console.print(f"\n[bold]{title}[/bold]")
                console.print(content)
    except ConnectionError:
        sys.exit(1)


# ---------------------------------------------------------------------------
# service subcommand group
# ---------------------------------------------------------------------------

@click.group()
def svc():
    """Scaffold and deploy Docker-based services (FastAPI first-class)."""
    pass


@svc.command(name="init")
@click.option("--domain", "-d", default=None,
              help="Public domain or hostname for this service (e.g. api.example.com). Required unless --internal is set.")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--port", type=int, help="App port inside container (auto-detected for FastAPI)")
@click.option("--image", "-i",
              help="Use a pre-built image instead of a build directive")
@click.option("--ingress-network", "ingress_networks", multiple=True,
              help="External Docker network used for ingress routing (repeat flag or use comma-separated values)")
@click.option("--global-ingress/--no-global-ingress", default=False,
              help="Attach the service to every configured ingress network instead of just one")
@click.option("--path-prefix", default=None,
              help="Route only traffic under this path prefix on the shared domain (e.g. /api/auth). "
                   "Allows multiple services to share one domain via path-based routing.")
@click.option("--internal", is_flag=True, default=False,
              help="Mark this service as internal-only: no caddy labels, no ingress network. "
                   "The container is reachable only by other containers on the same Docker network.")
@click.option("--force", is_flag=True,
              help="Overwrite existing Dockerfile / docker-compose.yml")
def service_init(domain, name, port, image, ingress_networks, global_ingress, path_prefix, internal, force):
    """Scaffold Dockerfile and docker-compose.yml for a FastAPI service.

    Run inside the project directory.  Detects FastAPI entrypoint automatically.
    """
    resolver = ServiceInitArgumentResolver()
    resolution = resolver.resolve(
        domain=domain,
        name=name,
        port=port,
        image=image,
        ingress_networks=ingress_networks,
        global_ingress=global_ingress,
        path_prefix=path_prefix,
        internal=internal,
        force=force,
    )
    if resolution is None:
        raise click.UsageError("--domain is required unless --internal is set")

    if not execute_service_init(resolution.context, console):
        sys.exit(1)


@svc.command(name="up")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--deploy-path", help="Remote deploy base path used by deploy push (for remote build context)")
@click.option("--rebuild", is_flag=True, default=False,
              help="Force a rebuild of the image from the remote build context even if the image already exists on the remote host")
@click.option("--missing-image-action", type=click.Choice(["ask", "push", "build", "abort"]), default="ask", show_default=True,
              help="Action when image is missing on the remote host")
@click.option("--auto-sync-context/--no-auto-sync-context", default=True,
              help="Automatically sync repository context on the remote host before remote build when needed")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--ssh-port", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--interactive/--no-interactive", default=True,
              help="Interactive mode")
def service_up(name, deploy_path, rebuild, missing_image_action, auto_sync_context,
                   host, ssh_port, username, key, password, use_config, interactive):
    """Deploy a service image to the remote host and register with ingress.

    Reads scaffolded routing/build intent from local docker-compose.yml.
    When the image does not exist on the remote host, the command can push or build it.
    """
    config = DeployConfig()
    resolver = ServiceDeployArgumentResolver(use_config=use_config)
    resolution = resolver.resolve(
        config,
        name=name,
        deploy_path=deploy_path,
        rebuild=rebuild,
        missing_image_action=missing_image_action,
        auto_sync_context=auto_sync_context,
        profile=ConnectionProfile(
            host=host,
            port=ssh_port,
            username=username,
            key=key,
            password=password,
        ),
        interactive=interactive,
    )
    if resolution is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    success, connection = execute_service_deploy(
        resolution.context,
        console,
        config=config,
        push_command=main,
        docker_push_command=docker_push,
    )
    if not success:
        sys.exit(1)

    persist_service_deploy_resolution(config, connection)


@svc.command(name="status")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def service_status(name, host, port, username, key, password, use_config):
    """Show the running status of a deployed service."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "service", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    service_name = name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            mgr = ServiceManager(ssh)
            status = mgr.get_status(service_name)
            if status:
                colour = "green" if status == "running" else "yellow"
                console.print(f"[{colour}]Service '{service_name}': {status}[/{colour}]")

                active_route_host = None
                routed_host_getter = getattr(mgr, "get_routed_host", None)
                if callable(routed_host_getter):
                    active_route_host = routed_host_getter(service_name)
                    if active_route_host:
                        console.print(f"[dim]Route host: {active_route_host}[/dim]")

                active_route_label = None
                routed_label_getter = getattr(mgr, "get_routed_site_label", None)
                if callable(routed_label_getter):
                    active_route_label = routed_label_getter(service_name)

                configured_domain = None
                metadata = None
                metadata_getter = getattr(mgr, "read_service_metadata", None)
                if callable(metadata_getter):
                    metadata = metadata_getter(service_name)
                    if isinstance(metadata, dict):
                        domain_value = metadata.get("domain")
                        if isinstance(domain_value, str) and domain_value.strip():
                            configured_domain = domain_value.strip()
                            console.print(f"[dim]Metadata domain: {configured_domain}[/dim]")

                if active_route_host:
                    if active_route_host == "localhost":
                        console.print("[dim]Ingress access: curl http://localhost/<path>[/dim]")
                        if active_route_label and active_route_label.startswith("http://"):
                            console.print(
                                "[dim]Ingress protocol: HTTP only (no localhost TLS certificate required)[/dim]"
                            )
                        else:
                            console.print(
                                "[dim]Ingress protocol: HTTPS redirect enabled for localhost[/dim]"
                            )
                    else:
                        console.print(
                            "[dim]Ingress access: curl -H \"Host: "
                            f"{active_route_host}\" http://localhost/<path>[/dim]"
                        )
                        if active_route_label and active_route_label.startswith("http://"):
                            console.print("[dim]Ingress protocol: HTTP only[/dim]")
                        else:
                            console.print("[dim]Ingress protocol: HTTPS managed by Caddy[/dim]")

                if isinstance(metadata, dict):
                    port_value = metadata.get("port")
                    if isinstance(port_value, int) and port_value > 0:
                        console.print(
                            f"[dim]In-network access: http://{service_name}:{port_value}/<path>[/dim]"
                        )

                if configured_domain and active_route_host and configured_domain != active_route_host:
                    console.print(
                        "[yellow]⚠ Routed host does not match persisted service domain metadata[/yellow]"
                    )
                    console.print(
                        "[dim]Redeploy with an explicit domain to update routing: "
                        "deploy service deploy --name "
                        f"{service_name} --domain <host>[/dim]"
                    )

                logs = mgr.get_logs(service_name, lines=20)
                if logs.strip():
                    console.print("\n[bold]Recent logs:[/bold]")
                    console.print(logs.rstrip())
            else:
                console.print(f"[yellow]Service '{service_name}' not found on remote host[/yellow]")
    except ConnectionError:
        sys.exit(1)


@svc.command(name="down")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
def service_down(name, host, port, username, key, password, use_config):
    """Stop and remove a deployed service's containers."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "service", host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    service_name = name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            if not ServiceManager(ssh).compose_down(service_name):
                sys.exit(1)
    except ConnectionError:
        sys.exit(1)


# ---------------------------------------------------------------------------
# monitor command
# ---------------------------------------------------------------------------

@click.command(name="monitor")
@click.option("--host", "host", help="Remote server hostname or IP")
@click.option("--port", "port", default=22, show_default=True, help="SSH port")
@click.option("--username", "username", help="SSH username")
@click.option("--key", "key", help="Path to SSH private key")
@click.option("--password", "password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--refresh-interval", default=5, show_default=True,
              help="Polling interval in seconds")
@click.option("--log-lines", default=120, show_default=True,
              help="How many lines to fetch for logs action")
@click.option("--command-timeout", default=10.0, show_default=True,
              help="Per-command SSH timeout in seconds")
@click.option("--action-timeout", default=15.0, show_default=True,
              help="Overall monitor action timeout in seconds")
def monitor(host, port, username, key, password, use_config, refresh_interval, log_lines,
                        command_timeout, action_timeout):
    """Run a long-running TUI monitor for proxy/services/networks/resources."""
    config = DeployConfig()
    ssh = _build_connection_from_config(
        config,
        "monitor",
        host,
        port,
        username,
        key,
        password,
        use_config,
        command_timeout,
    )
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        console.print("[dim]Use --host/--username or save config via push/pull/proxy/service first[/dim]")
        sys.exit(1)

    config.save_args(connection_args_from_connection(ssh), "monitor")

    try:
        from deploy.monitor.app import MonitorApp
    except ImportError as exc:
        console.print("[red]✗ Monitor dependencies are missing[/red]")
        console.print("[dim]Install project dependencies, including 'textual', then retry.[/dim]")
        console.print(f"[dim]{exc}[/dim]")
        sys.exit(1)

    console.print(Panel.fit(
        "[bold blue]Deploy Monitor[/bold blue]\n"
        f"Remote: {display_target(ssh)}",
        border_style="blue",
    ))
    connection_factory = LocalConnection if is_local_connection(ssh) else SSHConnection
    app = MonitorApp(
        host=ssh.host,
        port=ssh.port,
        username=ssh.username,
        key_filename=ssh.key_filename,
        password=password,
        refresh_interval=refresh_interval,
        log_lines=log_lines,
        command_timeout=command_timeout,
        action_timeout=action_timeout,
        ssh_factory=connection_factory,
    )
    app.run()


# ---------------------------------------------------------------------------
# CLI root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="deploy")
def cli():
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH."""
    pass


cli.add_command(main, name="push")
cli.add_command(pull, name="pull")
cli.add_command(show_config, name="show-config")
cli.add_command(clear_config, name="clear-config")
cli.add_command(docker_push, name="docker-push")
cli.add_command(proxy, name="proxy")
cli.add_command(svc, name="svc")
cli.add_command(monitor)

# Alias for test and programmatic access
service = svc


if __name__ == "__main__":
    cli()
