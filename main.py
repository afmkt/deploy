"""Git SSH Deploy Tool - Main CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from deploy import __version__
from deploy.config import DeployConfig
from deploy.image_build_flow import (
    ImageBuildArgumentResolver,
    execute_image_build,
    persist_image_build_resolution,
)
from deploy.image_push_flow import (
    ImagePushArgumentResolver,
    execute_image_push,
    persist_image_push_resolution,
)
from deploy.local import LocalConnection
from deploy.paths import REPOS_DIR
from deploy.proxy import ProxyManager
from deploy.proxy_up_flow import ProxyUpArgumentResolver, execute_proxy_up
from deploy.pull_flow import PullArgumentResolver, execute_pull, persist_pull_resolution
from deploy.push_flow import PushArgumentResolver, execute_push, persist_push_resolution
from deploy.service import ServiceManager
from deploy.service_deploy_flow import (
    ServiceDeployArgumentResolver,
    execute_service_deploy,
    persist_service_deploy_resolution,
    print_service_status_block,
)
from deploy.service_init_flow import ServiceInitArgumentResolver, execute_service_init
from deploy.session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from deploy.ssh import SSHConnection
from deploy.target import is_local_connection, proxy_healthcheck_url

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


def with_connection_options(*, include_use_config: bool = True):
    options = [
        click.option("--remote", help="Remote server hostname or IP."),
        click.option("--port", default=22, show_default=True, help="SSH port."),
        click.option("--username", help="SSH username."),
        click.option("--key", help="Path to SSH private key."),
        click.option("--password", help="SSH password."),
    ]
    if include_use_config:
        options.append(
            click.option(
                "--use-config/--no-use-config",
                default=True,
                show_default=True,
                help="Load arguments from config.",
            )
        )

    def decorator(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorator


def _build_connection_from_config(
    config: DeployConfig,
    section: str,
    remote: str | None,
    port: int,
    username: str | None,
    key: str | None,
    password: str | None,
    *,
    use_config: bool = True,
    command_timeout: float | None = None,
):
    completed = resolve_connection_profile(
        config,
        section,
        ConnectionProfile(
            host=remote or "",
            port=port,
            username=username or "",
            key=key or "",
            password=password,
        ),
        use_config=use_config,
        interactive=False,
    )
    if completed is None:
        return None
    return build_connection(completed, command_timeout)


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
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without pushing.")
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
    use_config: bool,
    dry_run: bool,
) -> None:
    _print_banner("Git SSH Deploy Tool", "Sync local Git repository to a remote repository")

    config = DeployConfig()
    resolver = PushArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=_interactive(ctx),
        use_config=use_config,
    )
    resolution = resolver.resolve(
        config,
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=_profile_from_options(remote, port, username, key, password),
    )
    if resolution and resolution.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_push(resolution.context, console, dry_run=dry_run)
    if not success:
        sys.exit(1)

    persist_push_resolution(config, resolution.context)
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")


@repo.command(name="pull")
@with_connection_options()
@click.option("--path", "deploy_path", default=DEFAULT_DEPLOY_PATH, show_default=True, help="Remote deploy path.")
@click.option("--repo-path", default=".", show_default=True, help="Path to the local Git repository.")
@click.option("--branch", help="Branch name to pull to.")
@click.option("--dry-run", is_flag=True, help="Validate connection and arguments without pulling.")
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
    use_config: bool,
    dry_run: bool,
) -> None:
    _print_banner("Git SSH Deploy Tool", "Pull changes from a remote repository")

    config = DeployConfig()
    resolver = PullArgumentResolver(
        default_repo_path=".",
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        interactive=_interactive(ctx),
        use_config=use_config,
    )
    resolution = resolver.resolve(
        config,
        repo_path=repo_path,
        deploy_path=deploy_path,
        profile=_profile_from_options(remote, port, username, key, password),
        branch=branch,
    )
    if resolution and resolution.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    if resolution is None:
        console.print("[red]✗ Username is required for remote connections[/red]")
        sys.exit(1)

    success = execute_pull(resolution.context, console, dry_run=dry_run)
    if not success:
        sys.exit(1)

    persist_pull_resolution(config, resolution.context)
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")


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
    use_config: bool,
    bootstrap: bool,
    ingress_networks: tuple[str, ...],
) -> None:
    config = DeployConfig()
    resolver = ProxyUpArgumentResolver(use_config=use_config)
    resolution = resolver.resolve(
        config,
        profile=_profile_from_options(remote, port, username, key, password),
        ingress_networks=ingress_networks,
        migrate_native_caddy=bootstrap,
        interactive=_interactive(ctx),
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required for remote connections[/red]")
        sys.exit(1)

    success, active_connection = execute_proxy_up(resolution.context, console, image_push)
    if not success or active_connection is None:
        sys.exit(1)

    args_to_save = connection_args_from_connection(active_connection)
    if resolution.context.networks:
        args_to_save["network"] = list(resolution.context.networks)
    config.save_args(args_to_save, "proxy.up")


@proxy.command(name="status")
@with_connection_options()
def proxy_status(remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool) -> None:
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy.status", remote, port, username, key, password, use_config=use_config)
    if ssh is None:
        console.print("[red]✗ Remote and username are required[/red]")
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
                console.print(f"[dim]Health check: {proxy_healthcheck_url(ssh)}[/dim]")
            else:
                console.print("[yellow]Ingress proxy container not found[/yellow]")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="down")
@with_connection_options()
def proxy_down(remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool) -> None:
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy.down", remote, port, username, key, password, use_config=use_config)
    if ssh is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            ProxyManager(ssh).down()
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="logs")
@with_connection_options()
@click.option("--lines", default=80, show_default=True, help="How many proxy log lines to fetch.")
def proxy_logs(remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool, lines: int) -> None:
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy.logs", remote, port, username, key, password, use_config=use_config)
    if ssh is None:
        console.print("[red]✗ Remote and username are required[/red]")
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
    config = DeployConfig()
    saved_args = config.load_args("svc.init")

    resolved_image = image or saved_args.get("image")
    if not resolved_image and _interactive(ctx):
        resolved_image = Prompt.ask("Service image")
    if not resolved_image:
        raise click.UsageError("--image is required unless it is available from config or interactive prompt")

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
        raise click.UsageError("--image is required unless it is available from config or interactive prompt")

    if not execute_service_init(resolution.context, console):
        sys.exit(1)

    config.save_args(
        {
            "image": resolution.context.image,
            "domain": resolution.context.domain,
            "name": resolution.context.service_name,
            "port": resolution.context.port,
            "network": list(resolution.context.ingress_networks),
            "global": resolution.context.global_ingress,
            "path_prefix": resolution.context.path_prefix,
        },
        "svc.init",
    )


@svc.command(name="up")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@with_connection_options()
def service_up(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool) -> None:
    config = DeployConfig()
    resolver = ServiceDeployArgumentResolver(use_config=use_config)
    resolution = resolver.resolve(
        config,
        name=service_name,
        profile=_profile_from_options(remote, port, username, key, password),
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    success, connection = execute_service_deploy(resolution.context, console)
    if not success:
        sys.exit(1)

    persist_service_deploy_resolution(config, connection)


@svc.command(name="status")
@click.option("--name", "service_name", help="Service name. Defaults to current directory name.")
@with_connection_options()
def service_status(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool) -> None:
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "svc.status", remote, port, username, key, password, use_config=use_config)
    if ssh is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

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
def service_down(service_name: str | None, remote: str | None, port: int, username: str | None, key: str | None, password: str | None, use_config: bool) -> None:
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "svc.down", remote, port, username, key, password, use_config=use_config)
    if ssh is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    effective_service_name = service_name or Path(".").resolve().name
    try:
        with managed_connection(ssh):
            if not ServiceManager(ssh).compose_down(effective_service_name):
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
@click.option("--dry-run", is_flag=True, help="Validate the transfer without sending the image.")
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
    use_config: bool,
    dry_run: bool,
) -> None:
    config = DeployConfig()
    resolver = ImagePushArgumentResolver(interactive=_interactive(ctx), use_config=use_config)
    resolution = resolver.resolve(
        config,
        image=image,
        profile=_profile_from_options(remote, port, username, key, password),
        platform=platform,
        registry_username=registry_username,
        registry_password=registry_password,
    )
    if resolution is None:
        console.print("[red]✗ Remote and username are required[/red]")
        sys.exit(1)

    if not execute_image_push(resolution.context, console, dry_run=dry_run):
        sys.exit(1)

    persist_image_push_resolution(config, resolution.context.profile)


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
    use_config: bool,
) -> None:
    config = DeployConfig()
    resolver = ImageBuildArgumentResolver(use_config=use_config)
    resolution = resolver.resolve(
        config,
        image=image_tag,
        deploy_path=deploy_path,
        default_deploy_path=DEFAULT_DEPLOY_PATH,
        profile=_profile_from_options(remote, port, username, key, password),
        interactive=_interactive(ctx),
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

    persist_image_build_resolution(config, resolution.context.profile)


cli.add_command(repo)
cli.add_command(proxy)
cli.add_command(svc)
cli.add_command(image)

# Aliases kept for programmatic imports and tests.
main = repo_push
pull = repo_pull
service = svc


if __name__ == "__main__":
    cli()
