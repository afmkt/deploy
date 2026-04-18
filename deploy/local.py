"""Local command execution backend compatible with the SSH connection interface."""

from __future__ import annotations

import getpass
import shlex
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


class LocalConnection:
    """Execute deployment commands on the current machine.

    This mirrors the small subset of the SSHConnection interface used by the
    rest of the codebase so existing managers can operate against either a
    remote host or the local machine.
    """

    is_local = True

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        username: Optional[str] = None,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        command_timeout: Optional[float] = None,
    ):
        # Always normalize 'local' to 'localhost'
        self.host = "localhost" if (host or "").strip().lower() == "local" else (host or "localhost")
        self.port = port
        self.username = username or getpass.getuser()
        self.password = password
        self.key_filename = key_filename
        self.command_timeout = command_timeout
        self.client = None
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        console.print(f"[green]✓ Using local host as {self.username}@{socket.gethostname()}[/green]")
        return True

    def disconnect(self):
        if self._connected:
            console.print("[yellow]Disconnected from local host[/yellow]")
        self._connected = False

    def execute(self, command: str, timeout: Optional[float] = None) -> tuple[int, str, str]:
        if not self._connected:
            console.print("[red]✗ Local host is not connected[/red]")
            return -1, "", "Not connected"

        effective_timeout = timeout if timeout is not None else self.command_timeout
        try:
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            timeout_str = f" after {effective_timeout}s" if effective_timeout else ""
            message = f"Command timed out{timeout_str}"
            console.print(f"[red]✗ {message}: {command}[/red]")
            return -1, "", message
        except Exception as exc:
            console.print(f"[red]✗ Local command execution failed: {exc}[/red]")
            return -1, "", str(exc)

    @staticmethod
    def copy_file(source_path: str, destination_path: str) -> None:
        """Copy a file locally, creating the destination directory if needed."""
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)

    @staticmethod
    def quoted_display_path(path: str) -> str:
        """Return a shell-escaped path for logging and debug output."""
        return shlex.quote(path)
