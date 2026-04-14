"""Git operations module using subprocess."""

import subprocess
from pathlib import Path
from typing import Optional
from rich.console import Console

console = Console()


class GitRepository:
    """Manages local Git repository operations."""

    def __init__(self, path: str = "."):
        """Initialize Git repository handler.

        Args:
            path: Path to the Git repository (default: current directory)
        """
        self.path = Path(path).resolve()

    def is_git_repo(self) -> bool:
        """Check if the directory is a Git repository.

        Returns:
            True if directory is a Git repository, False otherwise
        """
        git_dir = self.path / ".git"
        return git_dir.exists() and git_dir.is_dir()

    def get_remote_url(self, remote_name: str = "origin") -> Optional[str]:
        """Get the URL of a remote.

        Args:
            remote_name: Name of the remote (default: origin)

        Returns:
            Remote URL or None if remote doesn't exist
        """
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote_name],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def add_remote(self, name: str, url: str) -> bool:
        """Add a remote to the repository.

        Args:
            name: Remote name
            url: Remote URL

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if remote already exists
            existing_url = self.get_remote_url(name)
            if existing_url:
                if existing_url == url:
                    console.print(f"[yellow]Remote '{name}' already exists with same URL[/yellow]")
                    return True
                else:
                    # Update remote URL
                    subprocess.run(
                        ["git", "remote", "set-url", name, url],
                        cwd=self.path,
                        check=True
                    )
                    console.print(f"[green]✓ Updated remote '{name}' URL[/green]")
                    return True

            # Add new remote
            subprocess.run(
                ["git", "remote", "add", name, url],
                cwd=self.path,
                check=True
            )
            console.print(f"[green]✓ Added remote '{name}'[/green]")
            return True

        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to add remote: {e}[/red]")
            return False

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        """Push to remote repository.

        Args:
            remote: Remote name (default: origin)
            branch: Branch name (default: main)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current branch
            current_branch = self.get_current_branch()
            if current_branch:
                branch = current_branch

            console.print(f"[blue]Pushing to {remote}/{branch}...[/blue]")
            subprocess.run(
                ["git", "push", remote, branch],
                cwd=self.path,
                check=True
            )
            console.print(f"[green]✓ Pushed to {remote}/{branch}[/green]")
            return True

        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Push failed: {e}[/red]")
            return False

    def get_current_branch(self) -> Optional[str]:
        """Get the current branch name.

        Returns:
            Current branch name or None if not on a branch
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_current_revision(self) -> Optional[str]:
        """Get the current commit revision (short hash).

        Returns:
            Current commit short hash or None if not on a commit
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_repo_name(self) -> str:
        """Get the repository name from the path.

        Returns:
            Repository name
        """
        return self.path.name

    def validate(self) -> bool:
        """Validate that the directory is a valid Git repository.

        Returns:
            True if valid, False otherwise
        """
        if not self.path.exists():
            console.print(f"[red]✗ Path does not exist: {self.path}[/red]")
            return False

        if not self.is_git_repo():
            console.print(f"[red]✗ Not a Git repository: {self.path}[/red]")
            return False

        console.print(f"[green]✓ Valid Git repository: {self.path}[/green]")
        return True

    def pull(self, remote: str = "origin", branch: str = "main") -> bool:
        """Pull from remote repository.

        Args:
            remote: Remote name (default: origin)
            branch: Branch name (default: main)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current branch
            current_branch = self.get_current_branch()
            if current_branch:
                branch = current_branch

            console.print(f"[blue]Fetching from {remote}...[/blue]")
            subprocess.run(
                ["git", "fetch", remote],
                cwd=self.path,
                check=True
            )
            console.print(f"[green]✓ Fetched from {remote}[/green]")
            
            console.print(f"[blue]Merging {remote}/{branch}...[/blue]")
            subprocess.run(
                ["git", "merge", f"{remote}/{branch}"],
                cwd=self.path,
                check=True
            )
            console.print(f"[green]✓ Merged {remote}/{branch}[/green]")
            return True

        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Pull failed: {e}[/red]")
            return False

    def has_uncommitted_changes(self) -> bool:
        """Check whether local working tree has uncommitted changes.

        Returns:
            True when dirty or when status check fails, False when clean
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=True,
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to check local git status: {e}[/red]")
            # Be conservative when status cannot be determined.
            return True

    def checkout_branch(self, branch_name: str, create: bool = False) -> bool:
        """Checkout a branch.

        Args:
            branch_name: Name of the branch to checkout
            create: Whether to create the branch if it doesn't exist

        Returns:
            True if successful, False otherwise
        """
        try:
            if create:
                console.print(f"[blue]Checking out branch (create if missing): {branch_name}[/blue]")
                try:
                    subprocess.run(
                        ["git", "checkout", branch_name],
                        cwd=self.path,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    subprocess.run(
                        ["git", "checkout", "-b", branch_name],
                        cwd=self.path,
                        check=True,
                    )
            else:
                console.print(f"[blue]Checking out branch: {branch_name}[/blue]")
                subprocess.run(
                    ["git", "checkout", branch_name],
                    cwd=self.path,
                    check=True
                )
            console.print(f"[green]✓ Checked out branch: {branch_name}[/green]")
            return True

        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to checkout branch: {e}[/red]")
            return False
