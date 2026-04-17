"""Proxy up workflow argument resolution and execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from rich.console import Console
from rich.panel import Panel

from .config import DeployConfig
from .proxy import PROXY_IMAGE, ProxyManager
from .service import ServiceManager
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target, image_push_args_for_connection
from .ingress import normalize_ingress_networks


@dataclass(slots=True)
class ProxyUpExecutionContext:
    """Fully resolved arguments required to execute deploy proxy up."""

    profile: ConnectionProfile
    networks: tuple[str, ...]
    migrate_native_caddy: bool
    interactive: bool


@dataclass(slots=True)
class ProxyUpResolutionResult:
    """Resolved proxy-up execution context."""

    context: ProxyUpExecutionContext


class ProxyUpArgumentResolver:
    """Resolve proxy-up arguments from CLI input and config fallback."""

    def __init__(self, *, use_config: bool):
        self.use_config = use_config

    def resolve(
        self,
        config: DeployConfig,
        *,
        profile: ConnectionProfile,
        ingress_networks: Sequence[str],
        migrate_native_caddy: bool,
        interactive: bool,
    ) -> ProxyUpResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config, "proxy.up", profile, use_config=self.use_config
        )
        if completed_profile is None:
            return None

        return ProxyUpResolutionResult(
            context=ProxyUpExecutionContext(
                profile=completed_profile,
                networks=tuple(normalize_ingress_networks(ingress_networks)),
                migrate_native_caddy=migrate_native_caddy,
                interactive=interactive,
            )
        )


def execute_proxy_up(context: ProxyUpExecutionContext, console: Console, image_push_command: Any) -> tuple[bool, Any | None]:
    """Execute deploy proxy up using fully resolved arguments."""
    ssh = build_connection(context.profile)

    console.print(Panel.fit(
        "[bold blue]Proxy — up[/bold blue]\n"
        f"Ingress: {PROXY_IMAGE}\n"
        f"Remote: {display_target(ssh)}\n"
        f"Networks: {', '.join(context.networks)}",
        border_style="blue",
    ))

    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)
            from rich.prompt import Confirm

            native_caddy_content = None
            should_migrate_native_caddy = False

            console.print("\n[bold]Step 0: Check native Caddy[/bold]")
            native_caddy_found = mgr.native_caddy_exists()
            if native_caddy_found:
                console.print("[yellow]⚠ Native Caddy detected on remote host[/yellow]")
                if context.migrate_native_caddy:
                    if context.interactive:
                        should_migrate_native_caddy = Confirm.ask(
                            "Migrate native Caddy config and hand over ports 80/443 to docker-caddy-proxy?",
                            default=True,
                        )
                    else:
                        should_migrate_native_caddy = True
                else:
                    console.print("[yellow]Native Caddy migration is disabled by flag[/yellow]")
            else:
                console.print("[dim]No native Caddy detected[/dim]")

            console.print("\n[bold]Step 1: Ensure ingress network[/bold]")
            if not mgr.ensure_networks(context.networks):
                return False, None

            console.print("\n[bold]Step 2: Check proxy image[/bold]")
            if not mgr.proxy_image_exists_remote():
                console.print(f"[yellow]Image {PROXY_IMAGE} not found on remote host.[/yellow]")
                if context.interactive:
                    should_push_image = Confirm.ask(
                        f"Push {PROXY_IMAGE} to remote host now using image push?",
                        default=True,
                    )
                else:
                    console.print(f"[dim]Non-interactive mode: auto-pushing {PROXY_IMAGE}[/dim]")
                    should_push_image = True
                if should_push_image:
                    from click.testing import CliRunner

                    runner = CliRunner()
                    result = runner.invoke(
                        image_push_command,
                        image_push_args_for_connection(PROXY_IMAGE, ssh),
                        catch_exceptions=False,
                        standalone_mode=False,
                    )
                    if result.exit_code != 0:
                        console.print("[red]✗ image push failed[/red]")
                        return False, None
                else:
                    console.print(f"[yellow]Run: deploy image push --image {PROXY_IMAGE} first[/yellow]")
                    return False, None
            else:
                console.print(f"[green]✓ Image {PROXY_IMAGE} found on remote host[/green]")

            console.print("\n[bold]Step 3: Prepare bootstrap Caddyfile[/bold]")
            if should_migrate_native_caddy:
                native_caddy_content = mgr.read_native_caddyfile()
                if native_caddy_content and native_caddy_content.strip():
                    native_config_path = mgr.get_native_caddyfile_path()
                    console.print(f"[green]✓ Native Caddyfile found at {native_config_path}[/green]")

                    if mgr.native_config_uses_loopback_upstreams(native_caddy_content):
                        console.print(
                            "[yellow]Detected localhost loopback upstreams. "
                            "Rewriting upstreams to the host-side bridge address for bridge-mode compatibility.[/yellow]"
                        )
                        native_caddy_content = mgr.rewrite_native_caddyfile_for_bridge_mode(native_caddy_content)
                    else:
                        rewritten = mgr.rewrite_native_caddyfile_for_bridge_mode(native_caddy_content)
                        if rewritten != native_caddy_content:
                            console.print(
                                "[yellow]Rewrote loopback upstreams for bridge-mode host reachability[/yellow]"
                            )
                        native_caddy_content = rewritten
                else:
                    console.print(
                        "[red]✗ Native Caddy was detected, but its config could not be read. "
                        "Refusing to cut over with an empty bootstrap config.[/red]"
                    )
                    console.print(
                        "[yellow]Check the native Caddy config path and rerun proxy up after fixing it.[/yellow]"
                    )
                    return False, None
            else:
                native_caddy_content = ""

            if not mgr.write_bootstrap_caddyfile(native_caddy_content):
                return False, None

            console.print("\n[bold]Step 4: Deploy ingress compose file[/bold]")
            if not mgr.deploy_compose_file(context.networks):
                return False, None

            native_stopped = False
            if should_migrate_native_caddy:
                console.print("\n[bold]Step 5: Stop native Caddy[/bold]")
                if not mgr.stop_native_caddy():
                    return False, None
                native_stopped = True

            console.print("\n[bold]Step 6: Start ingress proxy[/bold]")
            if not mgr.up():
                if native_stopped:
                    console.print("[yellow]Attempting rollback: restart native Caddy...[/yellow]")
                    if mgr.start_native_caddy():
                        console.print("[yellow]Native Caddy restarted after proxy start failure[/yellow]")
                return False, None

            status = mgr.get_status()
            console.print("\n[bold]Step 7: Reconcile globally exposed services[/bold]")
            if not ServiceManager(ssh).reconcile_global_services(context.networks):
                return False, None

            console.print(f"\n[bold green]✓ Ingress proxy is {status}[/bold green]")
            return True, ssh
    except ConnectionError:
        return False, None


def persist_proxy_up_resolution(config: DeployConfig, connection: Any) -> dict[str, Any]:
    """Save resolved proxy-up connection args for later runs."""
    args_to_save = connection_args_from_connection(connection)
    config.save_args(args_to_save, "proxy.up")
    return args_to_save
