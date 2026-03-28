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
        success, bare_repo_url = remote.setup_deployment(repo_name)

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

        # Print summary
        working_dir_path = remote.get_working_dir_path(repo_name)
        print_summary(host, repo_name, bare_repo_url, working_dir_path)

    finally:
        ssh.disconnect()


if __name__ == "__main__":
    main()
