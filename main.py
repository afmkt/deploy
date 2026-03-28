"""Git SSH Deploy Tool - Main CLI entry point."""

import sys
import click
from rich.console import Console
from rich.panel import Panel

from deploy.git import GitRepository
from deploy.ssh import SSHConnection
from deploy.remote import RemoteServer
from deploy.utils import (
    prompt_connection_details,
    prompt_deploy_path,
    print_summary,
)

console = Console()


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default="/var/repos", help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
def main(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool):
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH.

    This tool automates repository setup, remote configuration, and deployment
    in a single command.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool[/bold blue]\n"
        "Sync local Git repository to remote server over SSH",
        border_style="blue"
    ))

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        # Non-interactive mode - require host and username
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Get deployment path
    if interactive and deploy_path == "/var/repos":
        deploy_path = prompt_deploy_path()

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to remote server[/bold]")
    ssh = SSHConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
    )

    if not ssh.connect():
        sys.exit(1)

    try:
        # Setup remote deployment
        console.print("\n[bold]Step 4: Setting up remote deployment[/bold]")
        remote = RemoteServer(ssh, deploy_path)
        current_branch = repo.get_current_branch() or "main"
        success, bare_repo_url = remote.setup_deployment(repo_name, current_branch)

        if not success:
            console.print("[red]✗ Failed to setup remote deployment[/red]")
            sys.exit(1)

        # Add remote to local repository
        console.print("\n[bold]Step 5: Configuring local remote[/bold]")
        remote_name = "deploy"
        if not repo.add_remote(remote_name, bare_repo_url):
            console.print("[red]✗ Failed to add remote[/red]")
            sys.exit(1)

        # Push to remote
        console.print("\n[bold]Step 6: Pushing to remote[/bold]")
        if not repo.push(remote_name):
            console.print("[red]✗ Failed to push to remote[/red]")
            sys.exit(1)

        # Update remote working directory
        console.print("\n[bold]Step 7: Updating remote working directory[/bold]")
        bare_repo_path = remote.get_bare_repo_path(repo_name)
        working_dir_path = remote.get_working_dir_path(repo_name)
        current_branch = repo.get_current_branch() or "main"
        if not remote.clone_or_update_working_dir(bare_repo_path, working_dir_path, current_branch):
            console.print("[red]✗ Failed to update remote working directory[/red]")
            sys.exit(1)

        # Print summary
        print_summary(host, repo_name, bare_repo_url, working_dir_path)

    finally:
        ssh.disconnect()


@click.command()
@click.option("--repo-path", "-r", default=".", help="Path to local Git repository")
@click.option("--host", "-h", help="Remote server hostname or IP")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", help="SSH username")
@click.option("--key", "-k", help="Path to SSH private key")
@click.option("--password", help="SSH password (not recommended, use key instead)")
@click.option("--deploy-path", "-d", default="/var/repos", help="Deploy path on remote server")
@click.option("--interactive/--no-interactive", default=True, help="Interactive mode")
@click.option("--commit/--no-commit", default=False, help="Commit changes in remote working directory")
@click.option("--pull/--no-pull", default=False, help="Pull from remote to local repository")
@click.option("--sync-remote/--no-sync-remote", default=False, help="Check if remote working dir is clean, commit changes, push to bare repo, then pull")
@click.option("--branch", "-b", help="Branch name to pull to (only used with --pull)")
def pull(repo_path: str, host: str, port: int, username: str, key: str,
         password: str, deploy_path: str, interactive: bool, commit: bool,
         pull: bool, sync_remote: bool, branch: str):
    """Pull from remote repository to local.

    This tool pulls changes from the remote repository to the local repository.
    """
    # Display banner
    console.print(Panel.fit(
        "[bold blue]Git SSH Deploy Tool - Pull Mode[/bold blue]\n"
        "Pull changes from remote repository to local",
        border_style="blue"
    ))

    # Validate local Git repository
    console.print("\n[bold]Step 1: Validating local repository[/bold]")
    repo = GitRepository(repo_path)
    if not repo.validate():
        sys.exit(1)

    repo_name = repo.get_repo_name()
    console.print(f"[green]Repository name: {repo_name}[/green]")

    # Get connection details
    console.print("\n[bold]Step 2: Configuring SSH connection[/bold]")
    if interactive and not host:
        conn_details = prompt_connection_details()
        host = conn_details["host"]
        port = conn_details["port"]
        username = conn_details["username"]
        key = conn_details["key_filename"]
        password = conn_details["password"]
    else:
        # Non-interactive mode - require host and username
        if not host:
            console.print("[red]✗ Host is required[/red]")
            sys.exit(1)
        if not username:
            console.print("[red]✗ Username is required[/red]")
            sys.exit(1)

    # Get deployment path
    if interactive and deploy_path == "/var/repos":
        deploy_path = prompt_deploy_path()

    # Connect to remote server
    console.print("\n[bold]Step 3: Connecting to remote server[/bold]")
    ssh = SSHConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
    )

    if not ssh.connect():
        sys.exit(1)

    try:
        # Get remote paths
        remote = RemoteServer(ssh, deploy_path)
        bare_repo_path = remote.get_bare_repo_path(repo_name)
        working_dir_path = remote.get_working_dir_path(repo_name)

        # Check if bare repository exists
        if not remote.directory_exists(bare_repo_path):
            console.print(f"[red]✗ Remote repository does not exist: {bare_repo_path}[/red]")
            sys.exit(1)

        # Optional: Sync remote working directory (check clean, commit, push, then pull)
        if sync_remote:
            console.print("\n[bold]Step 4: Checking if remote working directory is clean[/bold]")
            # Check if there are uncommitted changes
            exit_code, stdout, stderr = ssh.execute(
                f"cd {working_dir_path} && git status --porcelain"
            )
            if exit_code != 0:
                console.print(f"[red]✗ Failed to check git status: {stderr}[/red]")
                sys.exit(1)
            
            has_uncommitted = bool(stdout.strip())
            
            # Check if there are unpushed commits
            exit_code, stdout, stderr = ssh.execute(
                f"cd {working_dir_path} && git log origin/$(git rev-parse --abbrev-ref HEAD)..HEAD --oneline 2>/dev/null || echo ''"
            )
            has_unpushed = bool(stdout.strip()) if exit_code == 0 else False
            
            if has_uncommitted or has_unpushed:
                if has_uncommitted:
                    console.print("[yellow]Remote working directory has uncommitted changes[/yellow]")
                    # Commit changes
                    console.print("\n[bold]Step 5: Committing changes in remote working directory[/bold]")
                    if not remote.commit_remote_changes(working_dir_path):
                        console.print("[red]✗ Failed to commit changes in remote working directory[/red]")
                        sys.exit(1)
                
                if has_unpushed:
                    console.print("[yellow]Remote working directory has unpushed commits[/yellow]")
                
                # Push changes to bare repository
                console.print("\n[bold]Step 6: Pushing changes to bare repository[/bold]")
                if not remote.push_to_bare_repo(working_dir_path):
                    console.print("[red]✗ Failed to push changes to bare repository[/red]")
                    sys.exit(1)
            else:
                console.print("[green]✓ Remote working directory is clean and up to date[/green]")
        
        # Optional: Commit changes in remote working directory (without sync check)
        elif commit:
            console.print("\n[bold]Step 4: Committing changes in remote working directory[/bold]")
            if not remote.commit_remote_changes(working_dir_path):
                console.print("[red]✗ Failed to commit changes in remote working directory[/red]")
                sys.exit(1)

            # Push changes to bare repository
            console.print("\n[bold]Step 5: Pushing changes to bare repository[/bold]")
            if not remote.push_to_bare_repo(working_dir_path):
                console.print("[red]✗ Failed to push changes to bare repository[/red]")
                sys.exit(1)

        # Optional: Pull from remote to local
        if pull:
            step_num = 7 if sync_remote else 6
            console.print(f"\n[bold]Step {step_num}: Pulling from remote to local[/bold]")
            # Add remote if not exists
            remote_name = "deploy"
            bare_repo_url = f"ssh://{username}@{host}:{port}{bare_repo_path}"
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
                console.print("[red]✗ Failed to pull from remote[/red]")
                sys.exit(1)

        console.print("\n[green]✓ Pull operation completed successfully[/green]")

    finally:
        ssh.disconnect()


@click.group()
def cli():
    """Git SSH Deploy Tool - Sync local Git repository to remote server over SSH."""
    pass


cli.add_command(main, name="push")
cli.add_command(pull, name="pull")


if __name__ == "__main__":
    cli()
