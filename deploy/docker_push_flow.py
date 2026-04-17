"""Docker push workflow argument resolution and execution helpers."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from .config import DeployConfig
from .docker import DockerManager, _safe_image_filename
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target


@dataclass(slots=True)
class DockerPushExecutionContext:
    """Fully resolved arguments required to execute deploy docker-push."""

    image: str
    profile: ConnectionProfile
    platform: str | None
    registry_username: str | None
    registry_password: str | None
    interactive: bool


@dataclass(slots=True)
class DockerPushResolutionResult:
    """Resolved docker-push execution context plus config metadata."""

    context: DockerPushExecutionContext
    used_saved_args: bool


class DockerPushArgumentResolver:
    """Resolve docker-push arguments from CLI input, config fallback, and prompts."""

    def __init__(self, *, interactive: bool, use_config: bool):
        self.interactive = interactive
        self.use_config = use_config

    def resolve(
        self,
        config: DeployConfig,
        *,
        image: str,
        profile: ConnectionProfile,
        platform: str | None,
        registry_username: str | None,
        registry_password: str | None,
    ) -> DockerPushResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config,
            "docker-push",
            profile,
            use_config=self.use_config,
            interactive=self.interactive,
        )
        if completed_profile is None:
            return None

        return DockerPushResolutionResult(
            context=DockerPushExecutionContext(
                image=image,
                profile=completed_profile,
                platform=platform,
                registry_username=registry_username,
                registry_password=registry_password,
                interactive=self.interactive,
            ),
            used_saved_args=self.use_config,
        )


def execute_docker_push(
    context: DockerPushExecutionContext,
    console: Console,
    *,
    dry_run: bool = False,
) -> bool:
    """Execute deploy docker-push using fully resolved arguments."""
    console.print("\n[bold]Step 2: Connecting to remote host[/bold]")
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            docker_mgr = DockerManager(ssh)

            if dry_run:
                console.print("\n[bold]Dry Run Analysis[/bold]")
                if docker_mgr.is_docker_installed():
                    version = docker_mgr.get_docker_version()
                    console.print(f"  [green]✓ Docker is installed on remote host (version: {version})[/green]")
                else:
                    console.print("  [yellow]⚠ Docker is not installed on remote host[/yellow]")
                detected = docker_mgr.detect_remote_arch()
                effective_platform = context.platform or detected
                console.print(f"  Platform: {effective_platform or 'unknown'}")
                console.print(f"  Image: {context.image}")
                console.print("\n[green]✓ Dry run completed[/green]")
                return True

            console.print("\n[bold]Step 3: Checking Docker on remote host[/bold]")
            if not docker_mgr.is_docker_installed():
                console.print("[yellow]Docker is not installed on the remote host[/yellow]")
                if context.interactive:
                    from rich.prompt import Confirm

                    if not Confirm.ask("Install Docker now?", default=True):
                        console.print("[yellow]Docker installation skipped — cannot proceed[/yellow]")
                        return False
                if not docker_mgr.install_docker():
                    console.print("[red]✗ Failed to install Docker[/red]")
                    return False
            else:
                version = docker_mgr.get_docker_version()
                console.print(f"[green]✓ Docker is installed (version: {version})[/green]")

            console.print("\n[bold]Step 4: Detecting remote platform[/bold]")
            resolved_platform = context.platform
            if resolved_platform:
                console.print(f"[dim]Using user-supplied platform: {resolved_platform}[/dim]")
            else:
                resolved_platform = docker_mgr.detect_remote_arch()
                if not resolved_platform:
                    console.print("[red]✗ Could not detect remote architecture[/red]")
                    return False

            if context.registry_username and context.registry_password:
                console.print("\n[bold]Step 5: Authenticating with Docker registry[/bold]")
                if not docker_mgr.registry_login(
                    context.registry_username,
                    context.registry_password,
                    context.image,
                ):
                    return False

            step = 6 if (context.registry_username and context.registry_password) else 5
            console.print(f"\n[bold]Step {step}: Pulling image locally[/bold]")
            if not docker_mgr.pull_image(context.image, resolved_platform):
                return False

            step += 1
            console.print(f"\n[bold]Step {step}: Saving image to tarball[/bold]")
            tmpdir = tempfile.mkdtemp(prefix="deploy_docker_")
            tar_filename = _safe_image_filename(context.image)
            local_tar = os.path.join(tmpdir, tar_filename)
            if not docker_mgr.save_image(context.image, local_tar, resolved_platform):
                return False

            step += 1
            remote_tar = f"/tmp/{tar_filename}"
            console.print(f"\n[bold]Step {step}: Copying tarball to remote host[/bold]")
            if not docker_mgr.transfer_tarball(local_tar, remote_tar):
                return False

            step += 1
            console.print(f"\n[bold]Step {step}: Loading image on remote host[/bold]")
            if not docker_mgr.load_image(remote_tar, context.image):
                return False

            step += 1
            console.print(f"\n[bold]Step {step}: Cleaning up[/bold]")
            docker_mgr.cleanup_remote(remote_tar)
            try:
                os.remove(local_tar)
                os.rmdir(tmpdir)
                console.print("[dim]Cleaned up local tarball[/dim]")
            except OSError:
                pass

            console.print(
                f"\n[bold green]✓ Docker image '{context.image}' transferred successfully to {display_target(ssh)}[/bold green]"
            )
            return True
    except ConnectionError:
        return False


def persist_docker_push_resolution(
    config: DeployConfig,
    context: DockerPushExecutionContext,
) -> dict[str, Any]:
    """Save resolved docker-push arguments for later runs."""
    args_to_save = connection_args(context.profile)
    config.save_args(args_to_save, "docker-push")
    return args_to_save
