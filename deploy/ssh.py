"""SSH connection management module using paramiko."""

from typing import Optional
import paramiko
from rich.console import Console

console = Console()


class SSHConnection:
    """Manages SSH connections to remote servers."""

    def __init__(self, host: str, port: int = 22, username: Optional[str] = None,
                 password: Optional[str] = None, key_filename: Optional[str] = None):
        """Initialize SSH connection parameters.

        Args:
            host: Remote server hostname or IP address
            port: SSH port (default: 22)
            username: SSH username
            password: SSH password (optional if using key)
            key_filename: Path to SSH private key file (optional)
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.client = None

    def connect(self) -> bool:
        """Establish SSH connection to remote server.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
            }

            if self.key_filename:
                connect_kwargs["key_filename"] = self.key_filename
            elif self.password:
                connect_kwargs["password"] = self.password

            self.client.connect(**connect_kwargs)
            console.print(f"[green]✓ Connected to {self.host}[/green]")
            return True

        except paramiko.AuthenticationException:
            console.print(f"[red]✗ Authentication failed for {self.host}[/red]")
            return False
        except paramiko.SSHException as e:
            console.print(f"[red]✗ SSH error: {e}[/red]")
            return False
        except Exception as e:
            console.print(f"[red]✗ Connection failed: {e}[/red]")
            return False

    def disconnect(self):
        """Close SSH connection."""
        if self.client:
            self.client.close()
            console.print(f"[yellow]Disconnected from {self.host}[/yellow]")

    def execute(self, command: str) -> tuple[int, str, str]:
        """Execute command on remote server.

        Args:
            command: Command to execute

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self.client:
            console.print("[red]✗ Not connected to server[/red]")
            return -1, "", "Not connected"

        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            stdout_str = stdout.read().decode("utf-8")
            stderr_str = stderr.read().decode("utf-8")

            return exit_code, stdout_str, stderr_str

        except Exception as e:
            console.print(f"[red]✗ Command execution failed: {e}[/red]")
            return -1, "", str(e)

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
