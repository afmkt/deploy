"""Remote server operations module."""

import shlex
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

    @staticmethod
    def _q(value: str) -> str:
        """Shell-quote dynamic values used in remote commands."""
        return shlex.quote(value)

    def create_directory(self, path: str) -> bool:
        """Create directory on remote server.

        Args:
            path: Directory path to create

        Returns:
            True if successful, False otherwise
        """
        exit_code, stdout, stderr = self.ssh.execute(f"mkdir -p {self._q(path)}")
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
        exit_code, stdout, stderr = self.ssh.execute(f"test -d {self._q(path)} && echo 'exists'")
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
            head_path = self._q(f"{repo_path}/HEAD")
            exit_code, stdout, stderr = self.ssh.execute(f"test -f {head_path} && echo 'is_bare'")
            if "is_bare" in stdout:
                console.print(f"[yellow]Bare repository already exists: {repo_path}[/yellow]")
                return True
            else:
                console.print(f"[red]✗ Path exists but is not a bare repository: {repo_path}[/red]")
                return False

        # Create bare repository
        exit_code, stdout, stderr = self.ssh.execute(f"git init --bare {self._q(repo_path)}")
        if exit_code == 0:
            console.print(f"[green]✓ Initialized bare repository: {repo_path}[/green]")
            return True
        else:
            console.print(f"[red]✗ Failed to initialize bare repository: {stderr}[/red]")
            return False

    def clone_or_update_working_dir(self, bare_repo_path: str, working_dir_path: str, branch: str = "main") -> bool:
        """Clone or update working directory from bare repository.

        Args:
            bare_repo_path: Path to the bare repository
            working_dir_path: Path to the working directory
            branch: Branch name to checkout (default: main)

        Returns:
            True if successful, False otherwise
        """
        quoted_working_dir = self._q(working_dir_path)
        quoted_bare_repo = self._q(bare_repo_path)
        quoted_branch = self._q(branch)

        # Check if working directory exists
        if self.directory_exists(working_dir_path):
            # Update existing working directory
            console.print(f"[blue]Updating working directory: {working_dir_path}[/blue]")

            # Prevent pull conflicts caused by remote local edits.
            exit_code, stdout, stderr = self.ssh.execute(
                f"cd {quoted_working_dir} && git status --porcelain"
            )
            if exit_code != 0:
                console.print(f"[red]✗ Failed to check working directory status: {stderr}[/red]")
                return False
            if stdout.strip():
                console.print("[red]✗ Remote working directory has uncommitted changes; aborting update[/red]")
                return False
            
            # Checkout the specified branch
            exit_code, stdout, stderr = self.ssh.execute(
                f"cd {quoted_working_dir} && git checkout {quoted_branch}"
            )
            if exit_code != 0:
                # Try to create the branch if it doesn't exist
                exit_code, stdout, stderr = self.ssh.execute(
                    f"cd {quoted_working_dir} && git checkout -b {quoted_branch}"
                )
                if exit_code != 0:
                    console.print(f"[red]✗ Failed to checkout branch {branch}: {stderr}[/red]")
                    return False
            
            # Pull from the specified branch
            exit_code, stdout, stderr = self.ssh.execute(
                f"cd {quoted_working_dir} && git pull origin {quoted_branch}"
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
                f"git clone -b {quoted_branch} {quoted_bare_repo} {quoted_working_dir}"
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

    def get_remote_revision(self, working_dir_path: str) -> Optional[str]:
        """Get the current commit revision from remote working directory.

        Args:
            working_dir_path: Path to the working directory on remote server

        Returns:
            Current commit short hash or None if not on a commit
        """
        exit_code, stdout, stderr = self.ssh.execute(
            f"cd {self._q(working_dir_path)} && git rev-parse --short HEAD"
        )
        if exit_code == 0:
            return stdout.strip()
        return None

    def has_uncommitted_changes(self, working_dir_path: str) -> Optional[bool]:
        """Check if remote working directory has uncommitted changes."""
        exit_code, stdout, stderr = self.ssh.execute(
            f"cd {self._q(working_dir_path)} && git status --porcelain"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to check git status: {stderr}[/red]")
            return None
        return bool(stdout.strip())

    def has_unpushed_commits(self, working_dir_path: str) -> Optional[bool]:
        """Check if remote working directory has commits not pushed to origin."""
        command = (
            f"cd {self._q(working_dir_path)} && "
            "branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) && "
            "git rev-parse --verify \"origin/$branch\" >/dev/null 2>&1 && "
            "git log \"origin/$branch\"..HEAD --oneline"
        )
        exit_code, stdout, stderr = self.ssh.execute(command)
        if exit_code == 0:
            return bool(stdout.strip())

        # If upstream doesn't exist yet, treat as not having unpushed commits for sync checks.
        return False

    def commit_remote_changes(self, working_dir_path: str, message: str = "Update from remote") -> bool:
        """Commit changes in the remote working directory.

        Args:
            working_dir_path: Path to the working directory on remote server
            message: Commit message

        Returns:
            True if successful, False otherwise
        """
        # Check if working directory exists
        if not self.directory_exists(working_dir_path):
            console.print(f"[red]✗ Working directory does not exist: {working_dir_path}[/red]")
            return False

        # Check if there are changes to commit
        exit_code, stdout, stderr = self.ssh.execute(
            f"cd {self._q(working_dir_path)} && git status --porcelain"
        )
        if exit_code != 0:
            console.print(f"[red]✗ Failed to check git status: {stderr}[/red]")
            return False

        if not stdout.strip():
            console.print(f"[yellow]No changes to commit in remote working directory[/yellow]")
            return True

        quoted_message = self._q(message)
        # Add all changes and commit
        console.print(f"[blue]Committing changes in remote working directory...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"cd {self._q(working_dir_path)} && git add -A && git commit -m {quoted_message}"
        )
        if exit_code == 0:
            console.print(f"[green]✓ Committed changes in remote working directory[/green]")
            return True
        else:
            console.print(f"[red]✗ Failed to commit changes: {stderr}[/red]")
            return False

    def push_to_bare_repo(self, working_dir_path: str) -> bool:
        """Push changes from working directory to bare repository.

        Args:
            working_dir_path: Path to the working directory on remote server

        Returns:
            True if successful, False otherwise
        """
        # Check if working directory exists
        if not self.directory_exists(working_dir_path):
            console.print(f"[red]✗ Working directory does not exist: {working_dir_path}[/red]")
            return False

        # Push to origin (bare repository)
        console.print(f"[blue]Pushing changes to bare repository...[/blue]")
        exit_code, stdout, stderr = self.ssh.execute(
            f"cd {self._q(working_dir_path)} && git push origin HEAD"
        )
        if exit_code == 0:
            console.print(f"[green]✓ Pushed changes to bare repository[/green]")
            return True
        else:
            console.print(f"[red]✗ Failed to push to bare repository: {stderr}[/red]")
            return False

    def setup_deployment(self, repo_name: str, branch: str = "main") -> tuple[bool, str]:
        """Setup deployment for a repository.

        Args:
            repo_name: Repository name
            branch: Branch name to checkout (default: main)

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

        # Note: Working directory will be created after first push
        # because bare repository is empty and has no branches yet

        # Generate SSH URL for the bare repository
        username = self.ssh.username or "root"
        bare_repo_url = f"ssh://{username}@{self.ssh.host}:{self.ssh.port}{bare_repo_path}"

        return True, bare_repo_url
