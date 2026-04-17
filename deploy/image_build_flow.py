"""Image build-remote workflow: sync repo and build Docker image on remote host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .config import DeployConfig
from .git import GitRepository
from .remote import RemoteServer
from .service import ServiceManager
from .session import (
    ConnectionProfile,
    build_connection,
    connection_args_from_connection,
    managed_connection,
    resolve_connection_profile,
)
from .target import display_target, push_args_for_connection
from .paths import REPOS_DIR

console = Console()


@dataclass(slots=True)
class ImageBuildRemoteExecutionContext:
    """Fully resolved arguments required to execute deploy image build-remote."""

    image: str
    deploy_path: str | None
    profile: ConnectionProfile
    interactive: bool
    use_config: bool


@dataclass(slots=True)
class ImageBuildRemoteResolutionResult:
    """Resolved image-build-remote execution context plus config metadata."""

    context: ImageBuildRemoteExecutionContext


class ImageBuildRemoteArgumentResolver:
    """Resolve image-build-remote arguments from CLI input and config fallback."""

    def __init__(self, *, use_config: bool):
        self.use_config = use_config

    def resolve(
        self,
        config: DeployConfig,
        *,
        image: str,
        deploy_path: str | None,
        profile: ConnectionProfile,
        interactive: bool,
    ) -> ImageBuildRemoteResolutionResult | None:
        completed_profile = resolve_connection_profile(
            config, "image-build-remote", profile, use_config=self.use_config
        )
        if completed_profile is None:
            return None

        return ImageBuildRemoteResolutionResult(
            context=ImageBuildRemoteExecutionContext(
                image=image,
                deploy_path=deploy_path,
                profile=completed_profile,
                interactive=interactive,
                use_config=use_config,
            )
        )


def execute_image_build_remote(
    context: ImageBuildRemoteExecutionContext,
    console: Console,
    *,
    config: DeployConfig,
    push_command: Any,
) -> tuple[bool, Any | None]:
    """Execute deploy image build-remote: sync repo and build on remote host."""
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            console.print(Panel.fit(
                f"[bold blue]Image Build — {context.image}[/bold blue]\n"
                f"Remote: {display_target(ssh)}\n"
                f"Build method: remote (from synced repository)",
                border_style="blue",
            ))

            # Determine deploy path
            deploy_path = context.deploy_path
            if not deploy_path:
                if context.use_config:
                    saved_args = config.load_args("push")
                    deploy_path = saved_args.get("deploy_path") if saved_args else None
                if not deploy_path:
                    if context.interactive:
                        from .utils import prompt_deploy_path
                        deploy_path = prompt_deploy_path()
                    else:
                        console.print("[red]✗ Deploy path required (provide --deploy-path or enable config/interactive mode)[/red]")
                        return False, None

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
            work_dir_path = remote.get_work_dir_path(repo_name)
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


def _sync_repo_to_remote(ssh: Any, deploy_path: str, push_command: Any, console: Console) -> bool:
    """Run deploy push to sync repository to remote."""
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        push_command,
        push_args_for_connection(".", deploy_path, ssh),
        catch_exceptions=False,
        standalone_mode=False,
    )
    if result.exit_code != 0:
        console.print(f"[red]✗ Repository sync failed[/red]")
        return False
    return True


def persist_image_build_remote_resolution(config: DeployConfig, connection: Any) -> dict[str, Any]:
    """Save resolved image-build-remote connection args for later runs."""
    args_to_save = connection_args_from_connection(connection)
    config.save_args(args_to_save, "image-build-remote")
    return args_to_save
