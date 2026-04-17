"""Common utilities module."""

import os
import sys
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.prompt import Prompt, Confirm

console = Console()


def get_ssh_key_path() -> Optional[str]:
    """Get the default SSH key path.

    Returns:
        Path to SSH key or None if not found
    """
    ssh_dir = Path.home() / ".ssh"
    common_keys = ["id_ed25519", "id_rsa", "id_ecdsa"]

    for key_name in common_keys:
        key_path = ssh_dir / key_name
        if key_path.exists():
            return str(key_path)

    return None


def validate_host(host: str) -> bool:
    """Validate hostname or IP address.

    Args:
        host: Hostname or IP address

    Returns:
        True if valid, False otherwise
    """
    if not host:
        return False

    # Basic validation - not empty and doesn't contain invalid characters
    invalid_chars = [" ", "\t", "\n", "\r"]
    return not any(char in host for char in invalid_chars)


def validate_port(port: int) -> bool:
    """Validate port number.

    Args:
        port: Port number

    Returns:
        True if valid, False otherwise
    """
    return 1 <= port <= 65535


def validate_path(path: str) -> bool:
    """Validate file or directory path.

    Args:
        path: Path to validate

    Returns:
        True if valid, False otherwise
    """
    if not path:
        return False

    try:
        Path(path)
        return True
    except (ValueError, OSError):
        return False


def prompt_connection_details(
    default_host: Optional[str] = None,
    default_port: int = 22,
    default_username: Optional[str] = None,
) -> dict:
    """Prompt user for SSH connection details.

    Returns:
        Dictionary with connection details
    """
    console.print("\n[bold blue]SSH Connection Details[/bold blue]")

    host = (default_host or "").strip()
    if not host:
        host = Prompt.ask("Remote host (hostname or IP)", default="")
    if not validate_host(host):
        console.print("[red]✗ Invalid host[/red]")
        sys.exit(1)

    raw_port = Prompt.ask("Port", default=str(default_port))
    try:
        port = int(raw_port)
        if not validate_port(port):
            raise ValueError
    except ValueError:
        console.print("[red]✗ Invalid port number[/red]")
        sys.exit(1)

    username = Prompt.ask("Username", default=default_username or "")
    if not username:
        console.print("[red]✗ Username is required[/red]")
        sys.exit(1)

    # Ask for authentication method
    use_key = Confirm.ask("Use SSH key authentication?", default=True)

    key_filename = None
    password = None

    if use_key:
        default_key = get_ssh_key_path()
        if default_key:
            use_default = Confirm.ask(f"Use default key ({default_key})?", default=True)
            if use_default:
                key_filename = default_key
            else:
                key_filename = Prompt.ask("Path to SSH key")
        else:
            key_filename = Prompt.ask("Path to SSH key")

        if not key_filename or not Path(key_filename).exists():
            console.print("[red]✗ SSH key not found[/red]")
            sys.exit(1)
    else:
        password = Prompt.ask("Password", password=True)
        if not password:
            console.print("[red]✗ Password is required[/red]")
            sys.exit(1)

    return {
        "host": host,
        "port": port,
        "username": username,
        "key_filename": key_filename,
        "password": password,
    }


def prompt_deploy_path() -> str:
    """Prompt user for deployment path on remote server.

    Returns:
        Deployment path
    """
    console.print("\n[bold blue]Deployment Configuration[/bold blue]")
    deploy_path = Prompt.ask("Deploy path on remote server", default="/var/repos")

    if not validate_path(deploy_path):
        console.print("[red]✗ Invalid deploy path[/red]")
        sys.exit(1)

    return deploy_path


def print_summary(host: str, repo_name: str, bare_repo_url: str, working_dir_path: str,
                  local_revision: str | None = None, remote_revision: str | None = None):
    """Print deployment summary.

    Args:
        host: Remote server host
        repo_name: Repository name
        bare_repo_url: URL of the bare repository
        working_dir_path: Path to working directory on remote server
        local_revision: Local working directory revision
        remote_revision: Remote working directory revision
    """
    console.print("\n[bold green]✓ Deployment Complete![/bold green]")
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Remote Host: {host}")
    console.print(f"  Repository: {repo_name}")
    console.print(f"  Bare Repo URL: {bare_repo_url}")
    console.print(f"  Remote Working Directory: {working_dir_path}" +
                  (f"  Revision: {remote_revision}" if remote_revision else ""))
    console.print(f"  Local Working Directory: ." +
                  (f"  Revision: {local_revision}" if local_revision else ""))
    console.print(f"\n[bold]Next Steps:[/bold]")
    console.print(f"  1. Add the bare repo URL as a remote:")
    console.print(f"     git remote add deploy {bare_repo_url}")
    console.print(f"  2. Push your code:")
    console.print(f"     git push deploy main")
