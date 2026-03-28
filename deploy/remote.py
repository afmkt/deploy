"""Remote server operations module."""

from typing import Optional
from rich.console import Console
from .ssh import SSHConnection

console = Console()


class RemoteServer:
    """Manages remote server operations for deployment."""

    def __init__(self, ssh: SSHConnection, deploy_path: str = "/var/repos"):
        """Initialize remote server handler.

        Args:
            ssh: SSH connection to the remote server
            deploy_path: Base path for deployments on remote server
        """
        self.ssh = ssh
        self.deploy_path = deploy_path

    def create_directory(self, path: str) -> bool:
        """Create directory on remote server.

        Args:
            path: Directory path to create

        Returns:
            True if successful, False otherwise
        """
        exit_code, stdout, stderr = self.ssh.execute(f"mkdir -p {path}")
        if exit_code == 0:
            console.print(f"[green]✓ Created directory: {path}[/green]")
            return True
        else:
            console.print(f"[red]✗ Failed to create directory: {stderr}[/red]")
            return False

    def directory_exists(self, path: str) -> bool:
        """Check if directory exists on remote server.

        Args:
            path: Directory path to check

        Returns:
            True if directory exists, False otherwise
        """
        exit_code, stdout, stderr = self.ssh.execute(f"test -d {path} && echo 'exists'")
        return "exists" in stdout

    def init_bare_repo(self, repo_path: str) -> bool:
        """Initialize a bare Git repository on remote server.

        Args:
            repo_path: Path where to create the bare repository

        Returns:
            True if successful, False otherwise
        """
        # Check if bare repo already exists
        if self.directory_exists(repo_path):
            # Verify it's a bare repo
            exit_code, stdout, stderr = self.ssh.execute(f"test -f {repo_path}/HEAD && echo 'is_bare'")
            if "is_bare" in stdout:
                console.print(f"[yellow]Bare repository already exists: {repo_path}[/yellow]")
                return True
            else:
                console.print(f"[red]✗ Path exists but is not a bare repository: {repo_path}[/red]")
                return False

        # Create bare repository
        exit_code, stdout, stderr = self.ssh.execute(f"git init --bare {repo_path}")
        if exit_code == 0:
            console.print(f"[green]✓ Initialized bare repository: {repo_path}[/green]")
            return True
        else:
            console.print(f"[red]✗ Failed to initialize bare repository: {stderr}[/red]")
            return False

    def clone_or_update_working_dir(self, bare_repo_path: str, working_dir_path: str) -> bool:
        """Clone or update working directory from bare repository.

        Args:
            bare_repo_path: Path to the bare repository
            working_dir_path: Path to the working directory

        Returns:
            True if successful, False otherwise
        """
        # Check if working directory exists
        if self.directory_exists(working_dir_path):
            # Update existing working directory
            console.print(f"[blue]Updating working directory: {working_dir_path}[/blue]")
            exit_code, stdout, stderr = self.ssh.execute(
                f"cd {working_dir_path} && git pull"
            )
            if exit_code == 0:
                console.print(f"[green]✓ Updated working directory[/green]")
                return True
            else:
                console.print(f"[red]✗ Failed to update working directory: {stderr}[/red]")
                return False
        else:
            # Clone from bare repository
            console.print(f"[blue]Cloning working directory: {working_dir_path}[/blue]")
            exit_code, stdout, stderr = self.ssh.execute(
                f"git clone {bare_repo_path} {working_dir_path}"
            )
            if exit_code == 0:
                console.print(f"[green]✓ Cloned working directory[/green]")
                return True
            else:
                console.print(f"[red]✗ Failed to clone working directory: {stderr}[/red]")
                return False

    def get_bare_repo_path(self, repo_name: str) -> str:
        """Get the full path for a bare repository.

        Args:
            repo_name: Repository name

        Returns:
            Full path to the bare repository
        """
        return f"{self.deploy_path}/{repo_name}.git"

    def get_working_dir_path(self, repo_name: str) -> str:
        """Get the full path for a working directory.

        Args:
            repo_name: Repository name

        Returns:
            Full path to the working directory
        """
        return f"{self.deploy_path}/{repo_name}"

    def setup_deployment(self, repo_name: str) -> tuple[bool, str]:
        """Setup deployment for a repository.

        Args:
            repo_name: Repository name

        Returns:
            Tuple of (success, bare_repo_url)
        """
        # Create deploy directory if it doesn't exist
        if not self.create_directory(self.deploy_path):
            return False, ""

        # Setup bare repository
        bare_repo_path = self.get_bare_repo_path(repo_name)
        if not self.init_bare_repo(bare_repo_path):
            return False, ""

        # Setup working directory
        working_dir_path = self.get_working_dir_path(repo_name)
        if not self.clone_or_update_working_dir(bare_repo_path, working_dir_path):
            return False, ""

        # Generate SSH URL for the bare repository
        username = self.ssh.username or "root"
        bare_repo_url = f"ssh://{username}@{self.ssh.host}:{self.ssh.port}{bare_repo_path}"

        return True, bare_repo_url
