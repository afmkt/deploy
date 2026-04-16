"""Pull workflow argument resolution and execution helpers."""

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
from .target import construct_repo_url
from .utils import prompt_deploy_path


@dataclass(slots=True)
class PullExecutionContext:
    """Fully resolved arguments required to execute deploy pull."""

    repo_path: str
    deploy_path: str
    profile: ConnectionProfile
    commit: bool
    sync_remote: bool
    branch: str | None


@dataclass(slots=True)
class PullResolutionResult:
    """Resolved pull execution context plus config metadata."""

    context: PullExecutionContext
    used_saved_args: bool


class PullArgumentResolver:
    """Resolve pull arguments from CLI input, config fallback, and prompts."""

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
        commit: bool,
        sync_remote: bool,
        branch: str | None,
    ) -> PullResolutionResult | None:
        profile_result = load_connection_profile(
            config,
            "pull",
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

        return PullResolutionResult(
            context=PullExecutionContext(
                repo_path=resolved_repo_path,
                deploy_path=resolved_deploy_path,
                profile=completed_profile,
                commit=commit,
                sync_remote=sync_remote,
                branch=branch,
            ),
            used_saved_args=profile_result.used_saved_args,
        )


def execute_pull(context: PullExecutionContext, console: Console, *, dry_run: bool = False) -> bool:
    """Execute deploy pull using fully resolved arguments."""
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(context.repo_path)
    if not repo.validate():
        return False

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    console.print("\n[bold]Step 3: Connecting to target[/bold]")
    ssh = build_connection(context.profile)

    try:
        with managed_connection(ssh):
            if dry_run:
                console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
                return True

            remote = RemoteServer(ssh, context.deploy_path)
            bare_repo_path = remote.get_bare_repo_path(repo_name)
            working_dir_path = remote.get_working_dir_path(repo_name)

            if repo.has_uncommitted_changes():
                console.print("[red]✗ Local repository has uncommitted changes; commit or stash before pulling[/red]")
                return False

            if not remote.directory_exists(bare_repo_path):
                console.print(f"[red]✗ Deployment repository does not exist: {bare_repo_path}[/red]")
                return False

            if context.sync_remote:
                console.print("\n[bold]Step 4: Checking if deployment working directory is clean[/bold]")
                has_uncommitted = remote.has_uncommitted_changes(working_dir_path)
                if has_uncommitted is None:
                    return False

                has_unpushed = remote.has_unpushed_commits(working_dir_path)
                if has_unpushed is None:
                    return False

                if has_uncommitted or has_unpushed:
                    if has_uncommitted:
                        console.print("[yellow]Deployment working directory has uncommitted changes[/yellow]")
                        console.print("\n[bold]Step 5: Committing changes in deployment working directory[/bold]")
                        if not remote.commit_remote_changes(working_dir_path):
                            console.print("[red]✗ Failed to commit changes in deployment working directory[/red]")
                            return False

                    if has_unpushed:
                        console.print("[yellow]Deployment working directory has unpushed commits[/yellow]")

                    console.print("\n[bold]Step 6: Pushing changes to bare repository[/bold]")
                    if not remote.push_to_bare_repo(working_dir_path):
                        console.print("[red]✗ Failed to push changes to bare repository[/red]")
                        return False
                else:
                    console.print("[green]✓ Deployment working directory is clean and up to date[/green]")

            elif context.commit:
                console.print("\n[bold]Step 4: Committing changes in deployment working directory[/bold]")
                if not remote.commit_remote_changes(working_dir_path):
                    console.print("[red]✗ Failed to commit changes in deployment working directory[/red]")
                    return False

                console.print("\n[bold]Step 5: Pushing changes to bare repository[/bold]")
                if not remote.push_to_bare_repo(working_dir_path):
                    console.print("[red]✗ Failed to push changes to bare repository[/red]")
                    return False

            step_num = 7 if context.sync_remote else 6
            console.print(f"\n[bold]Step {step_num}: Pulling from deployment target to local[/bold]")
            remote_name = "deploy"
            bare_repo_url = construct_repo_url(bare_repo_path, ssh)
            if not repo.add_remote(remote_name, bare_repo_url):
                console.print("[red]✗ Failed to add remote[/red]")
                return False

            if context.branch and not repo.checkout_branch(context.branch, create=True):
                console.print(f"[red]✗ Failed to checkout branch: {context.branch}[/red]")
                return False

            if not repo.pull(remote_name):
                console.print("[red]✗ Failed to pull from deployment target[/red]")
                return False

            local_revision = repo.get_current_revision()
            remote_revision = remote.get_remote_revision(working_dir_path)
            console.print("\n[green]✓ Pull operation completed successfully[/green]")
            console.print("\n[bold]Revision Info:[/bold]")
            console.print(f"  Local: {local_revision or 'unknown'}")
            console.print(f"  Remote: {remote_revision or 'unknown'}")
            return True
    except ConnectionError:
        return False


def persist_pull_resolution(config: DeployConfig, context: PullExecutionContext) -> dict[str, Any]:
    """Save resolved pull arguments for later runs."""
    args_to_save: dict[str, Any] = {
        "repo_path": context.repo_path,
        "deploy_path": context.deploy_path,
    }
    args_to_save.update(connection_args(context.profile))
    config.save_args(args_to_save, "pull")
    return args_to_save
