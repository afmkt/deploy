"""Push workflow argument resolution and execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console

from .config import DeployConfig
from .git import GitRepository
from .remote import RemoteServer
from .session import (
    ALL_FALLBACK_SOURCES,
    ConnectionProfile,
    build_connection,
    complete_connection_profile,
    connection_args,
    load_connection_profile,
    load_defaulted_value,
    managed_connection,
)
from .target import import_source_label
from .utils import print_summary, prompt_deploy_path


@dataclass(slots=True)
class PushExecutionContext:
    """Fully resolved arguments required to execute deploy push."""

    repo_path: str
    deploy_path: str
    profile: ConnectionProfile


@dataclass(slots=True)
class PushResolutionResult:
    """Resolved push execution context plus config metadata."""

    context: PushExecutionContext
    used_saved_args: bool


class PushArgumentResolver:
    """Resolve push arguments from CLI input, config fallback, and prompts."""

    def __init__(
        self,
        *,
        default_repo_path: str,
        default_deploy_path: str,
        interactive: bool,
        use_config: bool,
    ):
        self.default_repo_path = default_repo_path
        self.default_deploy_path = default_deploy_path
        self.interactive = interactive
        self.use_config = use_config

    def resolve(
        self,
        config: DeployConfig,
        *,
        repo_path: str,
        deploy_path: str,
        profile: ConnectionProfile,
    ) -> PushResolutionResult | None:
        profile_result = load_connection_profile(
            config,
            "push",
            profile,
            use_config=self.use_config,
            fallback_sources=ALL_FALLBACK_SOURCES,
        )

        saved_args = profile_result.saved_args
        resolved_repo_path = load_defaulted_value(repo_path, self.default_repo_path, saved_args, "repo_path")
        resolved_deploy_path = load_defaulted_value(
            deploy_path,
            self.default_deploy_path,
            saved_args,
            "deploy_path",
        )

        completed_profile = complete_connection_profile(profile_result.profile, self.interactive)
        if completed_profile is None:
            return None

        if self.interactive and resolved_deploy_path == self.default_deploy_path:
            resolved_deploy_path = prompt_deploy_path()

        return PushResolutionResult(
            context=PushExecutionContext(
                repo_path=resolved_repo_path,
                deploy_path=resolved_deploy_path,
                profile=completed_profile,
            ),
            used_saved_args=profile_result.used_saved_args,
        )


def execute_push(context: PushExecutionContext, console: Console, *, dry_run: bool = False) -> bool:
    """Execute deploy push using fully resolved arguments."""
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(context.repo_path)
    if not repo.validate():
        return False

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    console.print("\n[bold]Step 3: Connecting to remote host[/bold]")
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            if dry_run:
                console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
                return True

            console.print("\n[bold]Step 4: Setting up remote repository[/bold]")
            remote = RemoteServer(ssh, context.deploy_path)
            current_branch = repo.get_current_branch() or "main"
            success, bare_repo_url = remote.setup_deployment(repo_name, current_branch)

            if not success:
                console.print("[red]✗ Failed to set up remote repository[/red]")
                return False

            console.print("\n[bold]Step 5: Configuring local remote[/bold]")
            remote_name = "deploy"
            if not repo.add_remote(remote_name, bare_repo_url):
                console.print("[red]✗ Failed to add remote[/red]")
                return False

            console.print("\n[bold]Step 6: Pushing to remote repository[/bold]")
            if not repo.push(remote_name):
                console.print("[red]✗ Failed to push to remote repository[/red]")
                return False

            console.print("\n[bold]Step 7: Updating deployment working directory[/bold]")
            bare_repo_path = remote.get_bare_repo_path(repo_name)
            working_dir_path = remote.get_working_dir_path(repo_name)
            current_branch = repo.get_current_branch() or "main"
            if not remote.clone_or_update_working_dir(bare_repo_path, working_dir_path, current_branch):
                console.print("[red]✗ Failed to update deployment working directory[/red]")
                return False

            local_revision = repo.get_current_revision()
            remote_revision = remote.get_remote_revision(working_dir_path)
            print_summary(
                import_source_label(ssh),
                repo_name,
                bare_repo_url,
                working_dir_path,
                local_revision=local_revision,
                remote_revision=remote_revision,
            )
            return True
    except ConnectionError:
        return False


def persist_push_resolution(config: DeployConfig, context: PushExecutionContext) -> dict[str, Any]:
    """Save resolved push arguments for later runs."""
    args_to_save: dict[str, Any] = {
        "repo_path": context.repo_path,
        "deploy_path": context.deploy_path,
    }
    args_to_save.update(connection_args(context.profile))
    config.save_args(args_to_save, "push")
    return args_to_save
