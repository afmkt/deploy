"""Image build workflow: sync repo and build a Docker image on the target host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .git import GitRepository
from .remote import RemoteServer
from .service import ServiceManager
from .session import (
    ConnectionProfile,
    build_connection,
    managed_connection,
    resolve_connection_profile,
    load_defaulted_value,
)
from .target import display_target, push_args_for_connection
from .paths import REPOS_DIR

from click.testing import CliRunner

console = Console()


@dataclass(slots=True)
class ImageBuildExecutionContext:
    """Fully resolved arguments required to execute `deploy image build`."""

    image: str
    deploy_path: str
    profile: ConnectionProfile


@dataclass(slots=True)
class ImageBuildResolutionResult:
    """Resolved image-build execution context plus config metadata."""

    context: ImageBuildExecutionContext


class ImageBuildArgumentResolver:
    """Resolve image-build arguments from CLI input and prompts."""

    def __init__(self, *, interactive: bool):
        self.interactive = interactive

    def resolve(
        self,
        *,
        image: str,
        deploy_path: str | None,
        default_deploy_path: str,
        profile: ConnectionProfile,
    ) -> ImageBuildResolutionResult | None:
        completed_profile = resolve_connection_profile(profile, interactive=self.interactive)
        if completed_profile is None:
            return None

        saved_args: dict[str, Any] = {}
        resolved_deploy_path = load_defaulted_value(
            deploy_path or default_deploy_path,
            default_deploy_path,
            saved_args,
            "path",
        )
        if self.interactive and resolved_deploy_path == default_deploy_path:
            from .utils import prompt_deploy_path
            resolved_deploy_path = prompt_deploy_path()

        return ImageBuildResolutionResult(
            context=ImageBuildExecutionContext(
                image=image,
                deploy_path=resolved_deploy_path,
                profile=completed_profile,
            )
        )


def _sync_repo_to_remote(ssh, deploy_path, push_command, console) -> bool:
    """Sync local repository to remote by invoking repo_push command."""
    runner = CliRunner()
    args = push_args_for_connection(
        repo_path=".",
        deploy_path=deploy_path,
        connection=ssh,
    )
    result = runner.invoke(
        push_command,
        args,
        catch_exceptions=False,
        standalone_mode=False,
    )
    if result.exit_code != 0:
        console.print(f"[red]✗ Repository sync failed[/red]")
        return False
    return True


def execute_image_build(
    context: ImageBuildExecutionContext,
    console: Console,
    *,
    push_command: Any,
) -> tuple[bool, Any | None]:
    """Execute `deploy image build`: sync repository and build on target host."""
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            console.print(Panel.fit(
                f"[bold blue]Image Build — {context.image}[/bold blue]\n"
                f"Remote: {display_target(ssh)}\n"
                f"Build method: remote (from synced repository)",
                border_style="blue",
            ))

            deploy_path = context.deploy_path

            # Validate local repo
            console.print("\n[bold]Step 1: Validating local repository[/bold]")
            repo = GitRepository(".")
            if not repo.validate():
                console.print("[red]✗ Current directory is not a valid Git repository[/red]")
                return False, None
            repo_name = repo.get_repo_name()
            local_revision = repo.get_current_revision()
            console.print(f"[green]✓ Repository: {repo_name} at {local_revision}[/green]")

            # Sync repository
            console.print("\n[bold]Step 2: Syncing repository to remote[/bold]")
            if not _sync_repo_to_remote(ssh, deploy_path, push_command, console):
                return False, None

            remote = RemoteServer(ssh, deploy_path)
            work_dir_path = remote.get_working_dir_path(repo_name)
            svc_mgr = ServiceManager(ssh)

            # Verify remote revision
            console.print("\n[bold]Step 3: Verifying remote repository state[/bold]")
            remote_revision = svc_mgr.get_context_revision(work_dir_path)
            if not remote_revision:
                console.print("[red]✗ Failed to read remote repository revision[/red]")
                return False, None

            if local_revision and remote_revision != local_revision:
                console.print(f"[red]✗ Revision mismatch: local {local_revision} vs remote {remote_revision}[/red]")
                return False, None

            console.print(f"[green]✓ Remote repository at {remote_revision}[/green]")

            # Build image on remote
            console.print(f"\n[bold]Step 4: Building image on remote[/bold]")
            if not svc_mgr.build_image_from_context(context.image, work_dir_path):
                console.print(f"[red]✗ Remote image build failed[/red]")
                return False, None

            console.print(f"\n[bold green]✓ Image '{context.image}' built on remote host[/bold green]")
            console.print(f"  Build context: {work_dir_path}")
            console.print(f"  Revision: {remote_revision}")

            return True, ssh
    except ConnectionError:
        console.print("[red]✗ Connection failed[/red]")
        return False, None
