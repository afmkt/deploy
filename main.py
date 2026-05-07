"""Git SSH Deploy Tool - Main CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich import print as rprint

from deploy import __version__
from deploy.image_build_flow import (
    ImageBuildArgumentResolver,
    execute_image_build,
)
from deploy.image_push_flow import (
    ImagePushArgumentResolver,
    execute_image_push,
)
from deploy.paths import REPOS_DIR
from deploy.proxy import ProxyManager
from deploy.proxy_up_flow import ProxyUpArgumentResolver, execute_proxy_up
from deploy.pull_flow import PullArgumentResolver, execute_pull
from deploy.push_flow import PushArgumentResolver, execute_push
from deploy.service import ServiceManager
from deploy.service_deploy_flow import (
    ServiceDeployArgumentResolver,
    execute_service_deploy,
    print_service_status_block,
)
from deploy.service_init_flow import ServiceInitArgumentResolver, execute_service_init
from deploy.session import (
    ConnectionProfile,
    build_connection,
    managed_connection,
    resolve_connection_profile,
)
from deploy.target import proxy_healthcheck_url
from deploy.diagnostic import Diagnostics

console = Console()
DEFAULT_DEPLOY_PATH = REPOS_DIR


def _interactive(ctx: click.Context) -> bool:
    if not isinstance(ctx.obj, dict):
        return True
    return bool(ctx.obj.get("interactive", True))


def _print_banner(title: str, subtitle: str) -> None:
    console.print(Panel.fit(
        f"[bold blue]{title}[/bold blue]\n{subtitle}",
        border_style="blue",
    ))


def _profile_from_options(
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
) -> ConnectionProfile:
    return ConnectionProfile(
        host=remote or "",
        port=port,
        username=username or "",
        key=key or "",
        password=password,
    )


def with_connection_options():
    options = [
        click.option("--remote", default="localhost", show_default=True, help="Remote server hostname or IP."),
        click.option("--port", default=22, show_default=True, help="SSH port."),
        click.option("--username", help="SSH username."),
        click.option("--key", help="Path to SSH private key."),
        click.option("--password", help="SSH password."),
    ]

    def decorator(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorator


@click.group()
@click.option("--non-interactive", is_flag=True, help="Disable interactive prompts.")
@click.version_option(__version__, prog_name="deploy")
@click.pass_context
def cli(ctx: click.Context, non_interactive: bool) -> None:
    """Git SSH Deploy Tool."""
    ctx.ensure_object(dict)
    ctx.obj["interactive"] = not non_interactive


@click.group()
def repo() -> None:
    """Sync repositories to and from a target host."""


@repo.command(name="push")
@with_connection_options()
@click.option("--path", "deploy_path", default=DEFAULT_DEPLOY_PATH, show_default=True, help="Remote deploy path.")
@click.option("--repo-path", default=".", show_default=True, help="Path to the local Git repository.")
@click.option("--non-interactive", is_flag=True, help="Disable interactive prompts.")
@click.option("--force", is_flag=True, help="Discard uncommitted changes in remote work directory before update.")
@click.pass_context
def repo_push(
    ctx: click.Context,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
    deploy_path: str,
    repo_path: str,
    non_interactive: bool,
    force: bool,
) -> None:
    _print_banner("Git SSH Deploy Tool", "Sync local Git repository to a remote repository")

    resolver = PushArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=_interactive(ctx) if not non_interactive else False,
        force=force,
    )
    resolution = resolver.resolve(
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=_profile_from_options(remote, port, username, key, password),
    )
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_push(resolution.context, console)
    if not success:
        sys.exit(1)


@repo.command(name="pull")
@with_connection_options()
@click.option("--path", "deploy_path", default=DEFAULT_DEPLOY_PATH, show_default=True, help="Remote deploy path.")
@click.option("--repo-path", default=".", show_default=True, help="Path to the local Git repository.")
@click.option("--branch", help="Branch name to pull to.")
@click.pass_context
def repo_pull(
    ctx: click.Context,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
    deploy_path: str,
    repo_path: str,
    branch: str | None,
) -> None:
    _print_banner("Git SSH Deploy Tool", "Pull changes from a remote repository")

    resolver = PullArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=_interactive(ctx),
    )
    resolution = resolver.resolve(
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=_profile_from_options(remote, port, username, key, password),
        branch=branch,
    )
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_pull(resolution.context, console)
    if not success:
        sys.exit(1)


@click.group()
def proxy() -> None:
    """Manage the ingress proxy."""


@proxy.command(name="up")
@with_connection_options()
@click.option("--bootstrap/--no-bootstrap", default=False, show_default=True, help="Bootstrap from native Caddy when present.")
@click.option("--network", "ingress_networks", multiple=True, help="Ingress network name. Repeat to attach multiple networks.")
@click.pass_context
def proxy_up(
    ctx: click.Context,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
    bootstrap: bool,
    ingress_networks: tuple[str, ...],
) -> None:
    resolver = ProxyUpArgumentResolver(interactive=_interactive(ctx))
    resolution = resolver.resolve(
        profile=_profile_from_options(remote, port, username, key, password),
        ingress_networks=ingress_networks,
        migrate_native_caddy=bootstrap,
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required for remote connections[/red]")
        sys.exit(1)

    success, active_connection = execute_proxy_up(resolution.context, console, image_push)
    if not success or active_connection is None:
        sys.exit(1)


@proxy.command(name="status")
@with_connection_options()
def proxy_status(remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
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
                console.print(f"[dim]Health check: {proxy_healthcheck_url(ssh)}[/dim]")
            else:
                console.print("[yellow]Ingress proxy container not found[/yellow]")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="down")
@with_connection_options()
def proxy_down(remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    try:
        with managed_connection(ssh):
            ProxyManager(ssh).down()
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="logs")
@with_connection_options()
@click.option("--lines", default=80, show_default=True, help="How many proxy log lines to fetch.")
def proxy_logs(remote: str | None, port: int, username: str | None, key: str | None, password: str | None, lines: int) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    try:
        with managed_connection(ssh):
            logs = ProxyManager(ssh).get_proxy_logs(lines=lines)
            if logs.strip():
                console.print(logs.rstrip())
            else:
                console.print("[yellow]No proxy logs available[/yellow]")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="restart")
@with_connection_options()
def proxy_restart(remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)
            console.print("[blue]Restarting proxy container...[/blue]")
            exit_code, _, stderr = ssh.execute("docker restart caddy-proxy")
            if exit_code != 0:
                console.print(f"[red]✗ Failed to restart proxy: {stderr.strip()}[/red]")
                sys.exit(1)
            console.print("[green]✓ Proxy restarted[/green]")
    except ConnectionError:
        sys.exit(1)


@click.group(name="svc")
def svc() -> None:
    """Scaffold and deploy Docker-based services."""


@svc.command(name="init")
@click.option("--domain", "-d", default=None, help="Public domain or hostname for this service.")
@click.option("--name", "-n", help="Service name. Defaults to current directory name.")
@click.option("--port", type=int, help="App port inside the container.")
@click.option("--image", "-i", help="Use a pre-built image.")
@click.option("--network", "ingress_networks", multiple=True, help="External ingress network. Repeat to attach multiple networks.")
@click.option("--global", "global_ingress", is_flag=True, default=False, help="Join every configured ingress network.")
@click.option("--path-prefix", default=None, help="Route only traffic under this path prefix.")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.pass_context
def service_init(
    ctx: click.Context,
    domain: str | None,
    name: str | None,
    port: int | None,
    image: str | None,
    ingress_networks: tuple[str, ...],
    global_ingress: bool,
    path_prefix: str | None,
    force: bool,
) -> None:
    resolved_image = image
    if not resolved_image and _interactive(ctx):
        resolved_image = Prompt.ask("Service image")
    if not resolved_image:
        raise click.UsageError("--image is required")

    resolver = ServiceInitArgumentResolver()
    resolution = resolver.resolve(
        domain=domain,
        name=name,
        port=port,
        image=resolved_image,
        ingress_networks=ingress_networks,
        global_ingress=global_ingress,
        path_prefix=path_prefix,
        force=force,
    )
    if resolution is None:
        raise click.UsageError("--image is required")

    if not execute_service_init(resolution.context, console):
        sys.exit(1)


@click.command()
@with_connection_options()
@click.option("--fix", is_flag=True, help="Attempt to fix connectivity issues by restarting proxy.")
def diagnose(remote: str | None, port: int, username: str | None, key: str | None, password: str | None, fix: bool) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    try:
        with managed_connection(ssh):
            diag = Diagnostics(ssh)
            
            console.print("[bold]Proxy Status:[/bold]")
            proxy_info = diag.check_proxy()
            if proxy_info.is_running:
                console.print(f"  [green]✓ Proxy is running ({proxy_info.status})[/green]")
            else:
                console.print("  [red]✗ Proxy is not running[/red]")
            
            if proxy_info.has_config:
                console.print("  [green]✓ Proxy has configuration[/green]")
                if proxy_info.config_preview:
                    console.print("  [dim]Config preview:[/dim]")
                    for line in proxy_info.config_preview.split('\n')[:5]:
                        console.print(f"    {line}")
            else:
                console.print("  [yellow]⚠ No configuration detected[/yellow]")
            
            console.print("\n[bold]Services with Caddy labels:[/bold]")
            services = diag.list_services_with_caddy_labels()
            if not services:
                console.print("  [yellow]⚠ No services with caddy labels found[/yellow]")
            else:
                for svc in services:
                    
                    info = diag.check_service_connectivity(svc)
                    # rprint(info)
                    status_icon = "✓" if info.reachable_from_proxy else "✗"
                    status_color = "green" if info.reachable_from_proxy else "red"
                    console.print(f"  [{status_color}]{status_icon}[/{status_color}] {svc}")
                    console.print(f"      Container: {info.container_status}")
                    console.print(f"      Has Caddy labels: {info.has_caddy_labels}")
                    if info.caddy_target:
                        console.print(f"      Caddy target: {info.caddy_target}")
                    if info.container_ip:
                        console.print(f"      Container IP: {info.container_ip}")
                    if info.caddy_host:
                        access_url = info.caddy_host
                        if info.path_prefix:
                            access_url = access_url + info.path_prefix
                        console.print(f"      Access URL: {access_url}")
                    if not info.reachable_from_proxy:
                        console.print("      [red]Not reachable from proxy[/red]")
            
            if fix:
                console.print("\n[bold]Attempting to fix connectivity...[/bold]")
                diag.fix_service_connectivity(services[0] if services else "unknown")
                
    except ConnectionError:
        sys.exit(1)


@svc.command(name="up")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@click.option("--sync/--no-sync", default=False, help="Sync the git repository before deploying.")
@click.option("--force", is_flag=True, help="Discard uncommitted changes in remote work directory before update.")
@click.option("--refresh", is_flag=True, help="Recreate the container to apply configuration changes.")
@with_connection_options()
def service_up(service_name: str | None, sync: bool, force: bool, refresh: bool, remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    resolver = ServiceDeployArgumentResolver()
    resolution = resolver.resolve(
        name=service_name,
        sync=sync,
        force=force,
        refresh=refresh,
        profile=_profile_from_options(remote, port, username, key, password),
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    success, connection = execute_service_deploy(resolution.context, console)
    if not success:
        sys.exit(1)


@svc.command(name="status")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@with_connection_options()
def service_status(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    effective_service_name = service_name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            mgr = ServiceManager(ssh)
            print_service_status_block(effective_service_name, mgr, console)
    except ConnectionError:
        sys.exit(1)


@svc.command(name="down")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@with_connection_options()
def service_down(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    effective_service_name = service_name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            if not ServiceManager(ssh).compose_down(effective_service_name):
                sys.exit(1)
    except ConnectionError:
        sys.exit(1)


@svc.command(name="restart")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@with_connection_options()
def service_restart(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None) -> None:
    profile = _profile_from_options(remote, port, username, key, password)
    resolved = resolve_connection_profile(profile, interactive=False)
    if resolved is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)
    ssh = build_connection(resolved)
    effective_service_name = service_name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            if not ServiceManager(ssh).restart(effective_service_name):
                sys.exit(1)
    except ConnectionError:
        sys.exit(1)


@click.group()
def image() -> None:
    """Deliver Docker images to a target host."""


@image.command(name="push")
@click.option("--image", required=True, help="Docker image to push.")
@click.option("--platform", help="Target platform override.")
@click.option("--registry-username", help="Registry username for private images.")
@click.option("--registry-password", help="Registry password for private images.")
@with_connection_options()
@click.option("--non-interactive", is_flag=True, help="Disable interactive prompts.")
@click.pass_context
def image_push(
    ctx: click.Context,
    image: str,
    platform: str | None,
    registry_username: str | None,
    registry_password: str | None,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
    non_interactive: bool,
) -> None:
    resolver = ImagePushArgumentResolver(interactive=_interactive(ctx) if not non_interactive else False)
    resolution = resolver.resolve(
        image=image,
        profile=_profile_from_options(remote, port, username, key, password),
        platform=platform,
        registry_username=registry_username,
        registry_password=registry_password,
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    if not execute_image_push(resolution.context, console):
        sys.exit(1)


@image.command(name="build")
@click.option("--tag", "image_tag", required=True, help="Docker image tag to build.")
@click.option("--path", "deploy_path", default=None, help="Remote deploy path used for repository sync.")
@with_connection_options()
@click.pass_context
def image_build(
    ctx: click.Context,
    image_tag: str,
    deploy_path: str | None,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
) -> None:
    resolver = ImageBuildArgumentResolver(interactive=_interactive(ctx))
    resolution = resolver.resolve(
        image=image_tag,
        deploy_path=deploy_path,
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        profile=_profile_from_options(remote, port, username, key, password),
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    success, _connection = execute_image_build(
        resolution.context,
        console,
        push_command=repo_push,
    )
    if not success:
        sys.exit(1)


cli.add_command(repo)
cli.add_command(proxy)
cli.add_command(svc)
cli.add_command(image)
cli.add_command(diagnose)

# Aliases kept for programmatic imports and tests.
main = repo_push
pull = repo_pull
service = svc


if __name__ == "__main__":
    cli()
