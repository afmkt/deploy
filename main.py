"""Git SSH Deploy Tool - Main CLI entry point."""

import json
import re
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
from deploy.session import (
    ConnectionProfile,
    build_connection,
    complete_connection_profile,
    connection_args,
    connection_args_from_connection,
    load_connection_profile,
    load_defaulted_value,
    managed_connection,
)
from deploy.target import (
    construct_repo_url,
    display_target,
    docker_push_args_for_connection,
    import_source_label,
    is_local_connection,
    push_args_for_connection,
    proxy_healthcheck_url,
)
from deploy.docker import DockerManager, _safe_image_filename
from deploy.proxy import ProxyManager, PROXY_IMAGE
from deploy.ingress import INGRESS_NETWORK, normalize_ingress_networks
from deploy.paths import REPOS_DIR
from deploy.service import (
    ServiceManager,
    detect_fastapi_entrypoint,
    render_dockerfile,
    render_service_metadata,
    render_service_compose,
)
from deploy.config import DeployConfig
from deploy.utils import (
    prompt_connection_details,
    prompt_deploy_path,
    print_summary,
)
from deploy import __version__

console = Console()
TARGET_CHOICES = click.Choice(["auto", "remote", "local"])
DEFAULT_DEPLOY_PATH = REPOS_DIR


def _build_connection_from_config(
    config: DeployConfig,
    section: str,
    target: str,
    host: str,
    port: int,
    username: str,
    key: str,
    password: str,
    use_config: bool = True,
    command_timeout: float | None = None,
):
    """Return a target connection, loading missing fields from saved config."""
    result = load_connection_profile(
        config,
        section,
        ConnectionProfile(
            target=target,
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        use_config=use_config,
        fallback_sources=["push", "pull", "docker-push", "proxy", "service", "monitor"],
    )
    completed = complete_connection_profile(result.profile, interactive=False)
    if completed is None:
        return None
    return build_connection(completed, command_timeout)


def _resolve_service_image(image: str | None, service_name: str, svc_mgr: ServiceManager) -> str | None:
    """Resolve service image from CLI, metadata, or deployed service state."""
    if image:
        return image

    resolved_image = _resolve_service_metadata_field("image", service_name, svc_mgr)
    if resolved_image:
        return resolved_image

    deployed_image_getter = getattr(svc_mgr, "get_deployed_image", None)
    if callable(deployed_image_getter):
        deployed_image = deployed_image_getter(service_name)
        if deployed_image:
            return deployed_image

    return _default_service_image_name(service_name)


def _default_service_image_name(service_name: str) -> str:
    """Return a predictable Docker image name for service-side builds."""
    normalized = re.sub(r"[^a-z0-9._/-]", "-", service_name.lower())
    normalized = normalized.strip("-./")
    if not normalized:
        normalized = "service"
    return f"{normalized}:latest"


def _resolve_service_domain(domain: str | None, service_name: str, svc_mgr: ServiceManager) -> str | None:
    """Resolve service domain from CLI, local metadata, or remote metadata."""
    if domain:
        return domain

    return _resolve_service_metadata_field("domain", service_name, svc_mgr)


def _load_local_service_metadata() -> dict:
    """Load local service metadata from the current working directory."""
    local_metadata_path = Path(".deploy-service.json")
    if not local_metadata_path.exists():
        return {}
    try:
        loaded = json.loads(local_metadata_path.read_text())
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _resolve_service_metadata_field(field: str, service_name: str, svc_mgr: ServiceManager) -> str | None:
    """Resolve a service metadata field from local metadata first, then target metadata."""
    local_metadata = _load_local_service_metadata()
    local_value = local_metadata.get(field)
    if isinstance(local_value, str) and local_value.strip():
        return local_value

    remote_metadata = svc_mgr.read_service_metadata(service_name)
    if not isinstance(remote_metadata, dict):
        return None
    remote_value = remote_metadata.get(field)
    if isinstance(remote_value, str) and remote_value.strip():
        return remote_value
    return None


def _resolve_service_deploy_path(
    config: DeployConfig,
    use_config: bool,
    deploy_path: str | None,
    interactive: bool,
) -> str | None:
    """Resolve the remote deploy path for repo-based build contexts."""
    if deploy_path:
        return deploy_path
    if use_config:
        for section in ("service", "push", "pull"):
            saved = config.load_args(section)
            if saved.get("deploy_path"):
                return saved["deploy_path"]
    if not interactive:
        return None
    from rich.prompt import Prompt

    return Prompt.ask(
        "Remote deploy path containing synced repository working directories",
        default=DEFAULT_DEPLOY_PATH,
    )


def _sync_repo_context_and_reconnect(ssh, deploy_path: str) -> bool:
    """Run deploy push for the active target and reconnect the session."""
    from click.testing import CliRunner

    runner = CliRunner()
    push_result = runner.invoke(
        main,
        push_args_for_connection(".", deploy_path, ssh),
        catch_exceptions=False,
        standalone_mode=False,
    )
    if push_result.exit_code != 0:
        console.print("[red]✗ deploy push failed[/red]")
        return False
    ssh.disconnect()
    return ssh.connect()


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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
            help="Whether to deploy to a remote SSH host or the local machine")
def main(repo_path: str, host: str, port: int, username: str, key: str,
        password: str, deploy_path: str, interactive: bool, use_config: bool, dry_run: bool,
        target: str):
    """Git SSH Deploy Tool - Sync local Git repository to a deployment target.

    This tool automates repository setup, remote configuration, and deployment
    in a single command.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool[/bold blue]\n"
        "Sync local Git repository to remote server over SSH",
        border_style="blue"
    ))

    # Load from config if requested
    config = DeployConfig()
    profile_result = load_connection_profile(
        config,
        "push",
        ConnectionProfile(
            target=target,
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        use_config=use_config,
    )
    if profile_result.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    saved_args = profile_result.saved_args
    profile = profile_result.profile
    deploy_path = load_defaulted_value(deploy_path, DEFAULT_DEPLOY_PATH, saved_args, "deploy_path")
    repo_path = load_defaulted_value(repo_path, ".", saved_args, "repo_path")

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring target[/bold]")
    completed_profile = complete_connection_profile(profile, interactive)
    if completed_profile is None:
        if not profile_result.profile.host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        console.print("[red]✗ Username is required[/red]")
        sys.exit(1)
    profile = completed_profile

    # Get deployment path
    if interactive and deploy_path == DEFAULT_DEPLOY_PATH:
        deploy_path = prompt_deploy_path()

    # Save arguments to config
    args_to_save = {
        "repo_path": repo_path,
        "deploy_path": deploy_path,
    }
    args_to_save.update(connection_args(profile))
    config.save_args(args_to_save, "push")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to target[/bold]")
    ssh = build_connection(profile)

    try:
        with managed_connection(ssh):
            if dry_run:
                console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
                return

        # Setup remote deployment
            console.print("\n[bold]Step 4: Setting up deployment target[/bold]")
            remote = RemoteServer(ssh, deploy_path)
            current_branch = repo.get_current_branch() or "main"
            success, bare_repo_url = remote.setup_deployment(repo_name, current_branch)

            if not success:
                console.print("[red]✗ Failed to setup deployment target[/red]")
                sys.exit(1)

            # Add remote to local repository
            console.print("\n[bold]Step 5: Configuring local remote[/bold]")
            remote_name = "deploy"
            if not repo.add_remote(remote_name, bare_repo_url):
                console.print("[red]✗ Failed to add remote[/red]")
                sys.exit(1)

            # Push to remote
            console.print("\n[bold]Step 6: Pushing to deployment target[/bold]")
            if not repo.push(remote_name):
                console.print("[red]✗ Failed to push to deployment target[/red]")
                sys.exit(1)

            # Update remote working directory
            console.print("\n[bold]Step 7: Updating deployment working directory[/bold]")
            bare_repo_path = remote.get_bare_repo_path(repo_name)
            working_dir_path = remote.get_working_dir_path(repo_name)
            current_branch = repo.get_current_branch() or "main"
            if not remote.clone_or_update_working_dir(bare_repo_path, working_dir_path, current_branch):
                console.print("[red]✗ Failed to update deployment working directory[/red]")
                sys.exit(1)

            # Get revision information
            local_revision = repo.get_current_revision()
            remote_revision = remote.get_remote_revision(working_dir_path)

            # Print summary
            print_summary(import_source_label(ssh), repo_name, bare_repo_url, working_dir_path,
                         local_revision=local_revision, remote_revision=remote_revision)
    except ConnectionError:
        sys.exit(1)


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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
            help="Whether to pull from a remote SSH host or the local machine")
def pull(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool, commit: bool,
        sync_remote: bool, branch: str, use_config: bool, dry_run: bool,
        target: str):
    """Pull from deployment target repository to local.

    This tool pulls changes from the remote repository to the local repository.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Pull Mode[/bold blue]\n"
        "Pull changes from remote repository to local",
        border_style="blue"
    ))

    # Load from config if requested
    config = DeployConfig()
    profile_result = load_connection_profile(
        config,
        "pull",
        ConnectionProfile(
            target=target,
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ),
        use_config=use_config,
    )
    if profile_result.used_saved_args:
        console.print("[dim]Loading arguments from config...[/dim]")
    saved_args = profile_result.saved_args
    profile = profile_result.profile
    deploy_path = load_defaulted_value(deploy_path, DEFAULT_DEPLOY_PATH, saved_args, "deploy_path")
    repo_path = load_defaulted_value(repo_path, ".", saved_args, "repo_path")

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring target[/bold]")
    completed_profile = complete_connection_profile(profile, interactive)
    if completed_profile is None:
        if not profile_result.profile.host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        console.print("[red]✗ Username is required[/red]")
        sys.exit(1)
    profile = completed_profile

    # Get deployment path
    if interactive and deploy_path == DEFAULT_DEPLOY_PATH:
        deploy_path = prompt_deploy_path()

    # Save arguments to config
    args_to_save = {
        "repo_path": repo_path,
        "deploy_path": deploy_path,
    }
    args_to_save.update(connection_args(profile))
    config.save_args(args_to_save, "pull")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to target[/bold]")
    ssh = build_connection(profile)

    try:
        with managed_connection(ssh):
            if dry_run:
                console.print("\n[green]✓ Dry run completed successfully - connection and arguments are valid[/green]")
                return

        # Get remote paths
            remote = RemoteServer(ssh, deploy_path)
            bare_repo_path = remote.get_bare_repo_path(repo_name)
            working_dir_path = remote.get_working_dir_path(repo_name)

        # Abort early on local dirty state to avoid accidental merge conflicts.
            if repo.has_uncommitted_changes():
                console.print("[red]✗ Local repository has uncommitted changes; commit or stash before pulling[/red]")
                sys.exit(1)

        # Check if bare repository exists
            if not remote.directory_exists(bare_repo_path):
                console.print(f"[red]✗ Deployment repository does not exist: {bare_repo_path}[/red]")
                sys.exit(1)

        # Optional: Sync remote working directory (check clean, commit, push, then pull)
            if sync_remote:
                console.print("\n[bold]Step 4: Checking if deployment working directory is clean[/bold]")
                has_uncommitted = remote.has_uncommitted_changes(working_dir_path)
                if has_uncommitted is None:
                    sys.exit(1)

                has_unpushed = remote.has_unpushed_commits(working_dir_path)
                if has_unpushed is None:
                    sys.exit(1)
            
                if has_uncommitted or has_unpushed:
                    if has_uncommitted:
                        console.print("[yellow]Deployment working directory has uncommitted changes[/yellow]")
                        # Commit changes
                        console.print("\n[bold]Step 5: Committing changes in deployment working directory[/bold]")
                        if not remote.commit_remote_changes(working_dir_path):
                            console.print("[red]✗ Failed to commit changes in deployment working directory[/red]")
                            sys.exit(1)
                
                    if has_unpushed:
                        console.print("[yellow]Deployment working directory has unpushed commits[/yellow]")
                
                    # Push changes to bare repository
                    console.print("\n[bold]Step 6: Pushing changes to bare repository[/bold]")
                    if not remote.push_to_bare_repo(working_dir_path):
                        console.print("[red]✗ Failed to push changes to bare repository[/red]")
                        sys.exit(1)
                else:
                    console.print("[green]✓ Deployment working directory is clean and up to date[/green]")
        
        # Optional: Commit changes in remote working directory (without sync check)
            elif commit:
                console.print("\n[bold]Step 4: Committing changes in deployment working directory[/bold]")
                if not remote.commit_remote_changes(working_dir_path):
                    console.print("[red]✗ Failed to commit changes in deployment working directory[/red]")
                    sys.exit(1)

                # Push changes to bare repository
                console.print("\n[bold]Step 5: Pushing changes to bare repository[/bold]")
                if not remote.push_to_bare_repo(working_dir_path):
                    console.print("[red]✗ Failed to push changes to bare repository[/red]")
                    sys.exit(1)

        # Pull from remote to local (default action)
            step_num = 7 if sync_remote else 6
            console.print(f"\n[bold]Step {step_num}: Pulling from deployment target to local[/bold]")
            # Add remote if not exists
            remote_name = "deploy"
            bare_repo_url = construct_repo_url(bare_repo_path, ssh)
            if not repo.add_remote(remote_name, bare_repo_url):
                console.print("[red]✗ Failed to add remote[/red]")
                sys.exit(1)

        # Checkout branch if specified
            if branch:
                if not repo.checkout_branch(branch, create=True):
                    console.print(f"[red]✗ Failed to checkout branch: {branch}[/red]")
                    sys.exit(1)

        # Pull from remote
            if not repo.pull(remote_name):
                console.print("[red]✗ Failed to pull from deployment target[/red]")
                sys.exit(1)

        # Get revision information
            local_revision = repo.get_current_revision()
            remote_revision = remote.get_remote_revision(working_dir_path)
            console.print("\n[green]✓ Pull operation completed successfully[/green]")
            console.print(f"\n[bold]Revision Info:[/bold]")
            console.print(f"  Local: {local_revision or 'unknown'}")
            console.print(f"  Remote: {remote_revision or 'unknown'}")
    except ConnectionError:
        sys.exit(1)


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
@click.option("--platform", help="Target platform override (e.g. linux/amd64, linux/arm64)")
@click.option("--registry-username", help="Docker registry username for private images")
@click.option("--registry-password", help="Docker registry password for private images")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--use-config/--no-use-config", default=False, help="Load arguments from config file")
@click.option("--dry-run", is_flag=True, help="Validate connection without transferring image")
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
                            help="Whether to transfer the image to a remote SSH host or the local machine")
def docker_push(image: str, host: str, port: int, username: str, key: str,
                password: str, platform: str | None, registry_username: str,
                registry_password: str, interactive: bool, use_config: bool,
                                dry_run: bool, target: str):
    """Push a Docker image to the deployment target.

    Pulls the image locally for the target architecture, saves it to a tarball,
    copies it to the target, and loads it there.
    """
    import tempfile
    import os

    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Docker Push[/bold blue]\n"
        "Transfer a Docker image to the deployment target",
        border_style="blue",
    ))

    # Load saved config if requested
    config = DeployConfig()
    if use_config:
        profile_result = load_connection_profile(
            config,
            "docker-push",
            ConnectionProfile(
                target=target,
                host=host,
                port=port,
                username=username,
                key=key,
                password=password,
            ),
            use_config=use_config,
            fallback_sources=["push", "pull"],
        )
        if profile_result.used_saved_args and not profile_result.fallback_source and profile_result.saved_args:
            console.print("[dim]Loading arguments from config...[/dim]")
        if profile_result.fallback_source and config.load_args("docker-push"):
            console.print(
                "[yellow]docker-push config is incomplete; trying SSH settings from push/pull.[/yellow]"
            )
            console.print(f"[dim]Loading SSH arguments from '{profile_result.fallback_source}' config...[/dim]")
        profile = profile_result.profile
    else:
        profile = ConnectionProfile(
            target=target,
            host=host,
            port=port,
            username=username,
            key=key,
            password=password,
        ).resolved()

    # Connection details
    console.print("\n[bold]Step 1: Configuring target[/bold]")
    completed_profile = complete_connection_profile(profile, interactive)
    if completed_profile is None:
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        console.print("[red]✗ Username is required[/red]")
        sys.exit(1)
    profile = completed_profile

    # Persist non-sensitive args
    config.save_args(connection_args(profile), "docker-push")
    console.print(f"[dim]Arguments saved to {config.get_config_path()}[/dim]")

    # Target connect
    console.print("\n[bold]Step 2: Connecting to target[/bold]")
    ssh = build_connection(profile)

    try:
        with managed_connection(ssh):
            docker_mgr = DockerManager(ssh)

            if dry_run:
                console.print("\n[bold]Dry Run Analysis[/bold]")
                if docker_mgr.is_docker_installed():
                    version = docker_mgr.get_docker_version()
                    console.print(f"  [green]✓ Docker is installed on target (version: {version})[/green]")
                else:
                    console.print("  [yellow]⚠ Docker is not installed on target[/yellow]")
                detected = docker_mgr.detect_remote_arch()
                effective_platform = platform or detected
                console.print(f"  Platform: {effective_platform or 'unknown'}")
                console.print(f"  Image: {image}")
                console.print("\n[green]✓ Dry run completed[/green]")
                return

        # Step 3: Ensure Docker is installed on remote
            console.print("\n[bold]Step 3: Checking Docker on target[/bold]")
            if not docker_mgr.is_docker_installed():
                console.print("[yellow]Docker is not installed on the target[/yellow]")
                if interactive:
                    from rich.prompt import Confirm
                    if not Confirm.ask("Install Docker now?", default=True):
                        console.print("[yellow]Docker installation skipped — cannot proceed[/yellow]")
                        sys.exit(1)
                if not docker_mgr.install_docker():
                    console.print("[red]✗ Failed to install Docker[/red]")
                    sys.exit(1)
            else:
                version = docker_mgr.get_docker_version()
                console.print(f"[green]✓ Docker is installed (version: {version})[/green]")

        # Step 4: Resolve target platform
            console.print("\n[bold]Step 4: Detecting target platform[/bold]")
            if platform:
                console.print(f"[dim]Using user-supplied platform: {platform}[/dim]")
            else:
                platform = docker_mgr.detect_remote_arch()
                if not platform:
                    console.print("[red]✗ Could not detect remote architecture[/red]")
                    sys.exit(1)

        # Step 5: Registry login (if credentials provided)
            if registry_username and registry_password:
                console.print("\n[bold]Step 5: Authenticating with Docker registry[/bold]")
                if not docker_mgr.registry_login(registry_username, registry_password, image):
                    sys.exit(1)

        # Step 6: Pull image locally for the target platform
            step = 6 if (registry_username and registry_password) else 5
            console.print(f"\n[bold]Step {step}: Pulling image locally[/bold]")
            if not docker_mgr.pull_image(image, platform):
                sys.exit(1)

        # Step 7: Save image to local tarball
            step += 1
            console.print(f"\n[bold]Step {step}: Saving image to tarball[/bold]")
            tmpdir = tempfile.mkdtemp(prefix="deploy_docker_")
            tar_filename = _safe_image_filename(image)
            local_tar = os.path.join(tmpdir, tar_filename)
            if not docker_mgr.save_image(image, local_tar, platform):
                sys.exit(1)

        # Step 8: Transfer tarball to remote
            step += 1
            remote_tar = f"/tmp/{tar_filename}"
            console.print(f"\n[bold]Step {step}: Copying tarball to target[/bold]")
            if not docker_mgr.transfer_tarball(local_tar, remote_tar):
                sys.exit(1)

        # Step 9: Load on remote
            step += 1
            console.print(f"\n[bold]Step {step}: Loading image on target[/bold]")
            if not docker_mgr.load_image(remote_tar, image):
                sys.exit(1)

        # Step 10: Cleanup
            step += 1
            console.print(f"\n[bold]Step {step}: Cleaning up[/bold]")
            docker_mgr.cleanup_remote(remote_tar)
            try:
                os.remove(local_tar)
                os.rmdir(tmpdir)
                console.print("[dim]Cleaned up local tarball[/dim]")
            except OSError:
                pass

            console.print(f"\n[bold green]✓ Docker image '{image}' transferred successfully to {display_target(ssh)}[/bold green]")
    except ConnectionError:
        sys.exit(1)


# ---------------------------------------------------------------------------
# proxy subcommand group
# ---------------------------------------------------------------------------

@click.group()
def proxy():
    """Manage the caddy-docker-proxy ingress container on the deployment target."""
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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to manage the proxy on a remote SSH host or the local machine")
@click.option("--interactive/--no-interactive", default=True,
              help="Interactive mode — disable for CI/CD pipelines")
def proxy_up(host, port, username, key, password, use_config, migrate_native_caddy, ingress_networks, target, interactive):
    """Start (or ensure running) the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", target, host, port, username, key, password, use_config)
    networks = normalize_ingress_networks(ingress_networks)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    console.print(Panel.fit(
        "[bold blue]Proxy — up[/bold blue]\n"
        f"Ingress: {PROXY_IMAGE}\n"
        f"Target: {display_target(ssh)}\n"
        f"Networks: {', '.join(networks)}",
        border_style="blue",
    ))

    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)
            from rich.prompt import Confirm

            native_caddy_found = False
            native_caddy_content = None
            should_migrate_native_caddy = False

        # Step 0: detect native Caddy
            console.print("\n[bold]Step 0: Check native Caddy[/bold]")
            native_caddy_found = mgr.native_caddy_exists()
            if native_caddy_found:
                console.print("[yellow]⚠ Native Caddy detected on target host[/yellow]")
                if migrate_native_caddy:
                    if interactive:
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

        # Step 1: ensure network
            console.print("\n[bold]Step 1: Ensure ingress network[/bold]")
            if not mgr.ensure_networks(networks):
                sys.exit(1)

        # Step 2: check image availability
            console.print("\n[bold]Step 2: Check proxy image[/bold]")
            if not mgr.proxy_image_exists_remote():
                console.print(f"[yellow]Image {PROXY_IMAGE} not found on target.[/yellow]")
                if interactive:
                    should_push_image = Confirm.ask(
                        f"Push {PROXY_IMAGE} to target now using docker-push?",
                        default=True,
                    )
                else:
                    console.print(f"[dim]Non-interactive mode: auto-pushing {PROXY_IMAGE}[/dim]")
                    should_push_image = True
                if should_push_image:
                    from click.testing import CliRunner
                    runner = CliRunner()
                    result = runner.invoke(
                        docker_push,
                        docker_push_args_for_connection(PROXY_IMAGE, ssh),
                        catch_exceptions=False,
                        standalone_mode=False,
                    )
                    if result.exit_code != 0:
                        console.print("[red]✗ docker-push failed[/red]")
                        sys.exit(1)
                else:
                    console.print(
                        f"[yellow]Run: deploy docker-push -i {PROXY_IMAGE} first[/yellow]"
                    )
                    sys.exit(1)
            else:
                console.print(f"[green]✓ Image {PROXY_IMAGE} found on target[/green]")

        # Step 3: prepare migration bootstrap (if needed)
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
                        rewritten_caddy_content = mgr.rewrite_native_caddyfile_for_bridge_mode(
                            native_caddy_content
                        )
                        native_caddy_content = rewritten_caddy_content
                    else:
                        rewritten_caddy_content = mgr.rewrite_native_caddyfile_for_bridge_mode(
                            native_caddy_content
                        )
                        if rewritten_caddy_content != native_caddy_content:
                            console.print(
                                "[yellow]Rewrote loopback upstreams for bridge-mode host reachability[/yellow]"
                            )
                        native_caddy_content = rewritten_caddy_content
                else:
                    console.print(
                        "[red]✗ Native Caddy was detected, but its config could not be read. "
                        "Refusing to cut over with an empty bootstrap config.[/red]"
                    )
                    console.print(
                        "[yellow]Check the native Caddy config path and rerun proxy up after fixing it.[/yellow]"
                    )
                    sys.exit(1)
            else:
                # Always ensure the mounted bootstrap file exists.
                native_caddy_content = ""

            if not mgr.write_bootstrap_caddyfile(native_caddy_content):
                sys.exit(1)

        # Step 4: deploy compose file
            console.print("\n[bold]Step 4: Deploy ingress compose file[/bold]")
            if not mgr.deploy_compose_file(networks):
                sys.exit(1)

        # Step 5: stop native Caddy before binding 80/443
            native_stopped = False
            if should_migrate_native_caddy:
                console.print("\n[bold]Step 5: Stop native Caddy[/bold]")
                if not mgr.stop_native_caddy():
                    sys.exit(1)
                native_stopped = True

        # Step 6: bring up
            console.print("\n[bold]Step 6: Start ingress proxy[/bold]")
            if not mgr.up():
                if native_stopped:
                    console.print("[yellow]Attempting rollback: restart native Caddy...[/yellow]")
                    if mgr.start_native_caddy():
                        console.print("[yellow]Native Caddy restarted after proxy start failure[/yellow]")
                sys.exit(1)

            status = mgr.get_status()
            console.print("\n[bold]Step 7: Reconcile globally exposed services[/bold]")
            if not ServiceManager(ssh).reconcile_global_services(networks):
                sys.exit(1)
            console.print(f"\n[bold green]✓ Ingress proxy is {status}[/bold green]")
            config.save_args(connection_args_from_connection(ssh), "proxy")
    except ConnectionError:
        sys.exit(1)


@proxy.command(name="status")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to inspect a remote SSH host or the local machine")
def proxy_status(host, port, username, key, password, use_config, target):
    """Show the status of the caddy-docker-proxy container."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", target, host, port, username, key, password, use_config)
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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to manage a remote SSH host or the local machine")
def proxy_down(host, port, username, key, password, use_config, target):
    """Stop the caddy-docker-proxy ingress stack."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", target, host, port, username, key, password, use_config)
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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to inspect a remote SSH host or the local machine")
def proxy_logs(host, port, username, key, password, use_config, lines, target):
    """Show recent docker-caddy-proxy container logs."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", target, host, port, username, key, password, use_config)
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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to diagnose a remote SSH host or the local machine")
def proxy_diagnose(host, port, username, key, password, use_config, lines, target):
    """Collect proxy and native Caddy diagnostics from the deployment target."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "proxy", target, host, port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    try:
        with managed_connection(ssh):
            mgr = ProxyManager(ssh)

            console.print(Panel.fit(
                "[bold blue]Proxy Diagnose[/bold blue]\n"
                "Target Caddy migration diagnostics",
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
def service():
    """Scaffold and deploy Docker-based services (FastAPI first-class)."""
    pass


@service.command(name="init")
@click.option("--domain", "-d", required=True,
              help="Public domain or hostname for this service (e.g. api.example.com)")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--port", type=int, help="App port inside container (auto-detected for FastAPI)")
@click.option("--image", "-i",
              help="Use a pre-built image instead of a build directive")
@click.option("--ingress-network", "ingress_networks", multiple=True,
              help="External Docker network used for ingress routing (repeat flag or use comma-separated values)")
@click.option("--global-ingress/--no-global-ingress", default=False,
              help="Attach the service to every configured ingress network instead of just one")
@click.option("--force", is_flag=True,
              help="Overwrite existing Dockerfile / docker-compose.yml")
def service_init(domain, name, port, image, ingress_networks, global_ingress, force):
    """Scaffold Dockerfile and docker-compose.yml for a FastAPI service.

    Run inside the project directory.  Detects FastAPI entrypoint automatically.
    """
    project_dir = Path(".")

    # Derive service name from directory if not given
    service_name = name or project_dir.resolve().name

    # Detect FastAPI entrypoint
    ep_file, app_str, default_port = detect_fastapi_entrypoint(project_dir)
    effective_port = port or default_port

    console.print(Panel.fit(
        f"[bold blue]Service init — {service_name}[/bold blue]\n"
        f"Domain: {domain}  Port: {effective_port}",
        border_style="blue",
    ))
    console.print(f"[dim]Detected entrypoint: {ep_file} → {app_str}[/dim]")

    # Dockerfile
    dockerfile_path = project_dir / "Dockerfile"
    if dockerfile_path.exists() and not force:
        console.print("[dim]Dockerfile already exists, skipping (use --force to overwrite)[/dim]")
    else:
        dockerfile_path.write_text(render_dockerfile(app_str, effective_port))
        console.print(f"[green]✓ Wrote {dockerfile_path}[/green]")

    # docker-compose.yml
    compose_path = project_dir / "docker-compose.yml"
    if compose_path.exists() and not force:
        console.print("[dim]docker-compose.yml already exists, skipping (use --force to overwrite)[/dim]")
    else:
        compose_content = render_service_compose(
            service_name=service_name,
            domain=domain,
            port=effective_port,
            image=image,
            ingress_networks=ingress_networks,
            exposure_scope="global" if global_ingress else "single",
        )
        compose_path.write_text(compose_content)
        console.print(f"[green]✓ Wrote {compose_path}[/green]")

    # Service metadata is written independently of compose file
    # (it must always exist for reconciliation to work correctly)
    metadata_path = project_dir / ".deploy-service.json"
    metadata_path.write_text(
        render_service_metadata(
            service_name=service_name,
            domain=domain,
            port=effective_port,
            image=image,
            ingress_networks=ingress_networks,
            exposure_scope="global" if global_ingress else "single",
        )
    )
    console.print(f"[green]✓ Wrote {metadata_path}[/green]")

    console.print("\n[bold green]✓ Service initialised[/bold green]")
    console.print(f"  Next: [dim]deploy service deploy --host <host> --image <image>[/dim]")


@service.command(name="deploy")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--image", "-i",
              help="Docker image name/tag to run or build on target (optional if resolvable from metadata/state)")
@click.option("--domain", "-d",
              help="Public domain / hostname")
@click.option("--port", type=int, default=8000,
              help="App port inside the container")
@click.option("--deploy-path", help="Remote deploy base path used by deploy push (for remote build context)")
@click.option("--missing-image-action", type=click.Choice(["ask", "push", "build", "abort"]), default="ask", show_default=True,
              help="Action when image is missing on target")
@click.option("--auto-sync-context/--no-auto-sync-context", default=True,
              help="Automatically sync repository context on target before remote build when needed")
@click.option("--ingress-network", "ingress_networks", multiple=True,
              help="External Docker network used for ingress routing (repeat flag or use comma-separated values)")
@click.option("--global-ingress/--no-global-ingress", default=False,
              help="Attach the service to every configured ingress network instead of just one")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--ssh-port", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--interactive/--no-interactive", default=True,
              help="Interactive mode")
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to deploy to a remote SSH host or the local machine")
def service_deploy(name, image, domain, port, deploy_path, missing_image_action, auto_sync_context,
                   ingress_networks, global_ingress, host, ssh_port, username, key,
                   password, use_config, interactive, target):
    """Deploy a service image to the deployment target and register with ingress.

    When the image does not exist on target, the command can push or build it.
    """
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "service", target, host, ssh_port, username, key, password, use_config)
    if ssh is None:
        console.print("[red]✗ Host and username are required[/red]")
        sys.exit(1)

    service_name = name or Path(".").resolve().name

    requested_networks = normalize_ingress_networks(ingress_networks)

    try:
        with managed_connection(ssh):
            svc_mgr = ServiceManager(ssh)
            proxy_mgr = ProxyManager(ssh)

            domain = _resolve_service_domain(domain, service_name, svc_mgr)
            if not domain:
                if not interactive:
                    console.print(
                        "[red]✗ Domain is required in non-interactive mode. Provide --domain or save domain in metadata.[/red]"
                    )
                    sys.exit(1)
                from rich.prompt import Prompt

                domain = Prompt.ask("Public domain / hostname")
                if not domain:
                    console.print("[red]✗ Domain is required[/red]")
                    sys.exit(1)

            console.print(Panel.fit(
                f"[bold blue]Service deploy — {service_name}[/bold blue]\n"
                f"Image: {image or '<auto-resolve>'}  Domain: {domain}  Port: {port}\n"
                f"Target: {display_target(ssh)}\n"
                f"Ingress: {'all configured networks' if global_ingress else ', '.join(requested_networks)}",
                border_style="blue",
            ))

        # Step 1: check ingress proxy is running
            console.print("\n[bold]Step 1: Check ingress proxy[/bold]")
            if not proxy_mgr.is_running():
                console.print("[yellow]⚠ Ingress proxy is not running[/yellow]")
                console.print("[dim]Run: deploy proxy up[/dim]")
                sys.exit(1)

        # Step 2: check image availability on remote
            console.print("\n[bold]Step 2: Check image on target[/bold]")
            resolved_image = _resolve_service_image(image, service_name, svc_mgr)
            if not resolved_image:
                if not interactive:
                    console.print(
                        "[red]✗ Image name is required in non-interactive mode. Provide --image or save image in metadata.[/red]"
                    )
                    sys.exit(1)
                from rich.prompt import Prompt

                resolved_image = Prompt.ask(
                    "Docker image name for target build/use",
                    default=_default_service_image_name(service_name),
                )
                if not resolved_image:
                    console.print("[red]✗ Image name is required[/red]")
                    sys.exit(1)

            image = resolved_image
            if not svc_mgr.image_exists_remote(image):
                console.print(f"[yellow]Image '{image}' not found on target.[/yellow]")
                choice = missing_image_action
                if choice == "ask":
                    if not interactive:
                        choice = "build"
                    else:
                        from rich.prompt import Prompt

                        choice = Prompt.ask(
                            "How would you like to provide the image?",
                            choices=["push", "build", "abort"],
                            default="build",
                        )
                if choice == "push":
                    from click.testing import CliRunner
                    runner = CliRunner()
                    result = runner.invoke(
                        docker_push,
                        docker_push_args_for_connection(image, ssh),
                        catch_exceptions=False,
                        standalone_mode=False,
                    )
                    if result.exit_code != 0:
                        console.print("[red]✗ docker-push failed[/red]")
                        sys.exit(1)
                    # Reconnect: docker-push opens its own target session
                    ssh.disconnect()
                    if not ssh.connect():
                        sys.exit(1)
                    svc_mgr = ServiceManager(ssh)
                    proxy_mgr = ProxyManager(ssh)
                elif choice == "build":
                    deploy_path = _resolve_service_deploy_path(config, use_config, deploy_path, interactive)
                    if not deploy_path:
                        console.print(
                            "[red]✗ Deploy path is required for remote build context in non-interactive mode. Provide --deploy-path or save push/pull deploy_path in config.[/red]"
                        )
                        sys.exit(1)
                    repo = GitRepository(".")
                    if not repo.validate():
                        console.print("[red]✗ Remote build requires a local git repository.[/red]")
                        sys.exit(1)
                    repo_name = repo.get_repo_name()
                    local_revision = repo.get_current_revision()
                    remote = RemoteServer(ssh, deploy_path)
                    context_path = remote.get_working_dir_path(repo_name)

                    if not svc_mgr.context_is_git_repo(context_path):
                        console.print(
                            f"[yellow]Build context not found on target at {context_path}.[/yellow]"
                        )
                        should_sync = auto_sync_context
                        if not should_sync and interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to target now using deploy push?",
                                choices=["yes", "no"],
                                default="yes",
                            ) == "yes"
                        if should_sync:
                            if not _sync_repo_context_and_reconnect(ssh, deploy_path):
                                sys.exit(1)
                            svc_mgr = ServiceManager(ssh)
                            remote = RemoteServer(ssh, deploy_path)
                            context_path = remote.get_working_dir_path(repo_name)
                        else:
                            console.print(
                                "[red]✗ Cannot build without synced remote context. Run deploy push first or enable --auto-sync-context.[/red]"
                            )
                            sys.exit(1)

                    remote_revision = svc_mgr.get_context_revision(context_path)
                    if not remote_revision:
                        console.print("[red]✗ Failed to read remote build context revision[/red]")
                        sys.exit(1)
                    if local_revision and remote_revision != local_revision:
                        console.print(
                            f"[yellow]Revision mismatch: local {local_revision} vs target {remote_revision}[/yellow]"
                        )
                        should_sync = auto_sync_context
                        if not should_sync and interactive:
                            from rich.prompt import Prompt

                            should_sync = Prompt.ask(
                                "Sync repository to target now using deploy push?",
                                choices=["yes", "no"],
                                default="yes",
                            ) == "yes"
                        if should_sync:
                            if not _sync_repo_context_and_reconnect(ssh, deploy_path):
                                sys.exit(1)
                            svc_mgr = ServiceManager(ssh)
                            remote = RemoteServer(ssh, deploy_path)
                            context_path = remote.get_working_dir_path(repo_name)
                        else:
                            console.print(
                                "[red]✗ Remote context must match local revision before build. Run deploy push first or enable --auto-sync-context.[/red]"
                            )
                            sys.exit(1)

                    if not svc_mgr.context_is_git_repo(context_path):
                        console.print("[red]✗ Synced remote context is still unavailable[/red]")
                        sys.exit(1)
                    remote_revision = svc_mgr.get_context_revision(context_path)
                    if local_revision and remote_revision != local_revision:
                        console.print(
                            "[red]✗ Remote context revision still mismatches local repository after sync[/red]"
                        )
                        sys.exit(1)

                    if not svc_mgr.build_image_from_context(image, context_path):
                        sys.exit(1)
                else:
                    console.print(
                        f"[yellow]Run: deploy docker-push {' '.join(docker_push_args_for_connection(image, ssh))} first[/yellow]"
                    )
                    sys.exit(1)
            else:
                console.print(f"[green]✓ Image '{image}' found on target[/green]")

            effective_networks = (
                proxy_mgr.get_configured_ingress_networks() if global_ingress else requested_networks
            )

        # Step 3: ensure service directory and compose file
            console.print("\n[bold]Step 3: Upload service compose[/bold]")
            if not svc_mgr.ensure_service_dir(service_name):
                sys.exit(1)
            compose_content = render_service_compose(
                service_name=service_name,
                domain=domain,
                port=port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if global_ingress else "single",
            )
            if not svc_mgr.upload_compose(service_name, compose_content):
                sys.exit(1)
            metadata_content = render_service_metadata(
                service_name=service_name,
                domain=domain,
                port=port,
                image=image,
                ingress_networks=effective_networks,
                exposure_scope="global" if global_ingress else "single",
            )
            if not svc_mgr.upload_metadata(service_name, metadata_content):
                sys.exit(1)

        # Step 4: start service
            console.print("\n[bold]Step 4: Start service[/bold]")
            if not svc_mgr.compose_up(service_name):
                sys.exit(1)

            status = svc_mgr.get_status(service_name)
            container_ip = svc_mgr.get_container_ip(service_name)

            console.print(f"\n[bold green]✓ Service '{service_name}' deployed[/bold green]")
            console.print(f"  Domain : {domain}")
            console.print(f"  Status : {status}")
            console.print(f"  Exposure: {'global' if global_ingress else 'single-network'}")
            if container_ip:
                console.print(f"  Container IP: {container_ip}")
            config.save_args(connection_args_from_connection(ssh), "service")
    except ConnectionError:
        sys.exit(1)


@service.command(name="status")
@click.option("--name", "-n", help="Service name (defaults to current directory name)")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password")
@click.option("--use-config/--no-use-config", default=True,
              help="Load SSH args from saved config")
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
              help="Whether to inspect a remote SSH host or the local machine")
def service_status(name, host, port, username, key, password, use_config, target):
    """Show the running status of a deployed service."""
    config = DeployConfig()
    ssh = _build_connection_from_config(config, "service", target, host, port, username, key, password, use_config)
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
                logs = mgr.get_logs(service_name, lines=20)
                if logs.strip():
                    console.print("\n[bold]Recent logs:[/bold]")
                    console.print(logs.rstrip())
            else:
                console.print(f"[yellow]Service '{service_name}' not found on target[/yellow]")
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
@click.option("--target", type=TARGET_CHOICES, default="auto", show_default=True,
                            help="Whether to monitor a remote SSH host or the local machine")
def monitor(host, port, username, key, password, use_config, refresh_interval, log_lines,
                        command_timeout, action_timeout, target):
    """Run a long-running TUI monitor for proxy/services/networks/resources."""
    config = DeployConfig()
    ssh = _build_connection_from_config(
        config,
        "monitor",
        target,
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
        f"Target: {display_target(ssh)}",
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
cli.add_command(service, name="service")
cli.add_command(monitor)


if __name__ == "__main__":
    cli()
