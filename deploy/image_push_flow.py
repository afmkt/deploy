"""Image push workflow: transfer a pre-built local Docker image to remote host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console

from .config import DeployConfig
from .docker import DockerManager, _safe_image_filename
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target

console = Console()


@dataclass(slots=True)
class ImagePushExecutionContext:
    """Fully resolved arguments required to execute deploy image push."""

    image: str
    profile: ConnectionProfile
    platform: str | None
    registry_username: str | None
    registry_password: str | None
    interactive: bool


@dataclass(slots=True)
class ImagePushResolutionResult:
    """Resolved image-push execution context plus config metadata."""

    context: ImagePushExecutionContext
    used_saved_args: bool


class ImagePushArgumentResolver:
    """Resolve image-push arguments from CLI input and config fallback."""

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
    ) -> ImagePushResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config,
            "image-push",
            profile,
            use_config=self.use_config,
        )
        if completed_profile is None:
            return None

        return ImagePushResolutionResult(
            context=ImagePushExecutionContext(
                image=image,
                profile=completed_profile,
                platform=platform,
                registry_username=registry_username,
                registry_password=registry_password,
                interactive=self.interactive,
            ),
            used_saved_args=self.use_config,
        )


def execute_image_push(
    context: ImagePushExecutionContext,
    console: Console,
    *,
    dry_run: bool = False,
) -> bool:
    """Execute deploy image push: transfer local image to remote host."""
    console.print(f"\n[bold]Image Push — {context.image}[/bold]")
    console.print(f"Target: {display_target(build_connection(context.profile))}")

    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            docker_mgr = DockerManager(ssh)

            if dry_run:
                console.print("\n[bold]Dry Run Analysis[/bold]")
                if docker_mgr.is_docker_installed():
                    console.print("[green]✓ Docker is installed on remote host[/green]")
                    if docker_mgr.load_image_from_stdin(f"{context.image}\n", True):
                        console.print(f"[green]✓ Would successfully load '{context.image}' on remote[/green]")
                    else:
                        console.print(f"[red]✗ Cannot load image {context.image}[/red]")
                        return False
                else:
                    console.print("[red]✗ Docker is not installed on remote host[/red]")
                    return False
                return True

            console.print("\n[bold]Step 1: Checking local image[/bold]")
            if not docker_mgr.image_exists_locally(context.image):
                console.print(f"[red]✗ Image '{context.image}' not found locally[/red]")
                return False
            console.print(f"[green]✓ Image '{context.image}' found locally[/green]")

            console.print("\n[bold]Step 2: Preparing image for transfer[/bold]")
            safe_filename = _safe_image_filename(context.image)
            
            if not docker_mgr.save_image_to_file(
                context.image,
                safe_filename,
                context.platform,
                context.registry_username,
                context.registry_password,
            ):
                console.print(f"[red]✗ Failed to prepare image for transfer[/red]")
                return False

            console.print("\n[bold]Step 3: Transferring to remote host[/bold]")
            if not docker_mgr.upload_and_load_image(safe_filename):
                console.print(f"[red]✗ Failed to transfer and load image on remote[/red]")
                return False

            console.print(f"\n[bold green]✓ Image '{context.image}' transferred to remote host[/bold green]")
            return True

    except Exception as e:
        console.print(f"[red]✗ Error during image push: {e}[/red]")
        return False


def persist_image_push_resolution(config: DeployConfig, connection: Any) -> dict[str, Any]:
    """Save resolved image-push connection args for later runs."""
    args_to_save = connection_args_from_connection(connection)
    config.save_args(args_to_save, "image-push")
    return args_to_save
