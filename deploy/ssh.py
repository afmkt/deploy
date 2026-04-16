"""SSH connection management module using paramiko."""

import socket
from typing import Any, Optional
import paramiko
from rich.console import Console

console = Console()


class SSHConnection:
    """Manages SSH connections to remote servers."""

    def __init__(self, host: str, port: int = 22, username: Optional[str] = None,
                 password: Optional[str] = None, key_filename: Optional[str] = None,
                 command_timeout: Optional[float] = None):
        """Initialize SSH connection parameters.

        Args:
            host: Remote server hostname or IP address
            port: SSH port (default: 22)
            username: SSH username
            password: SSH password (optional if using key)
            key_filename: Path to SSH private key file (optional)
            command_timeout: Default timeout in seconds for remote commands
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.command_timeout = command_timeout
        self.client: Optional[paramiko.SSHClient] = None

    def _target(self) -> str:
        """Return a human-friendly SSH target string."""
        username = self.username or "<unknown-user>"
        host = self.host or "<unknown-host>"
        return f"{username}@{host}:{self.port}"

    def _auth_method(self) -> str:
        """Return a human-friendly auth description."""
        if self.key_filename:
            return f"key {self.key_filename}"
        if self.password:
            return "password"
        return "default SSH auth"

    def connect(self) -> bool:
        """Establish SSH connection to remote server.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs: dict[str, Any] = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
            }

            if self.key_filename:
                connect_kwargs["key_filename"] = self.key_filename
            elif self.password:
                connect_kwargs["password"] = self.password

            self.client.connect(**connect_kwargs)
            console.print(f"[green]✓ Connected to {self._target()}[/green]")
            return True

        except paramiko.AuthenticationException:
            console.print(
                f"[red]✗ Authentication failed for {self._target()} using {self._auth_method()}[/red]"
            )
            return False
        except (socket.timeout, TimeoutError) as e:
            console.print(
                f"[red]✗ Connection timed out for {self._target()} using {self._auth_method()}: {e}[/red]"
            )
            console.print(
                "[yellow]Check the host, port, network path, and whether --use-config loaded stale SSH settings.[/yellow]"
            )
            return False
        except FileNotFoundError as e:
            console.print(
                f"[red]✗ SSH key file not found for {self._target()}: {e}[/red]"
            )
            return False
        except paramiko.SSHException as e:
            console.print(f"[red]✗ SSH error for {self._target()} using {self._auth_method()}: {e}[/red]")
            return False
        except Exception as e:
            console.print(
                f"[red]✗ Connection failed for {self._target()} using {self._auth_method()}: {e}[/red]"
            )
            return False

    def disconnect(self):
        """Close SSH connection."""
        if self.client:
            self.client.close()
            console.print(f"[yellow]Disconnected from {self._target()}[/yellow]")

    def execute(self, command: str, timeout: Optional[float] = None) -> tuple[int, str, str]:
        """Execute command on remote server.

        Args:
            command: Command to execute
            timeout: Optional timeout in seconds for this command. If omitted,
                falls back to the connection's default command timeout.

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self.client:
            console.print("[red]✗ Not connected to server[/red]")
            return -1, "", "Not connected"

        try:
            effective_timeout = timeout if timeout is not None else self.command_timeout
            stdin, stdout, stderr = self.client.exec_command(command, timeout=effective_timeout)
            if effective_timeout is not None:
                stdout.channel.settimeout(effective_timeout)
                stderr.channel.settimeout(effective_timeout)
            exit_code = stdout.channel.recv_exit_status()
            stdout_str = stdout.read().decode("utf-8", errors="replace")
            stderr_str = stderr.read().decode("utf-8", errors="replace")

            return exit_code, stdout_str, stderr_str

        except socket.timeout:
            effective_timeout = timeout if timeout is not None else self.command_timeout
            timeout_str = f" after {effective_timeout}s" if effective_timeout else ""
            message = f"Command timed out{timeout_str}"
            console.print(f"[red]✗ {message}: {command}[/red]")
            return -1, "", message

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
