"""Shared CLI session helpers for connection-oriented commands."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping
from rich.prompt import Prompt

from .local import LocalConnection
from .ssh import SSHConnection
from .target import is_local_host, resolve_target
from .utils import prompt_connection_details

DEFAULT_SSH_PORT = 22


@dataclass(slots=True)
class ConnectionProfile:
    """Normalized connection settings for a command run."""

    host: str = ""
    port: int = DEFAULT_SSH_PORT
    username: str = ""
    key: str = ""
    password: str | None = None

    @property
    def mode(self) -> str:
        """Return the normalized transport mode for this profile."""
        return resolve_target(self.host)

    def resolved(self) -> "ConnectionProfile":
        return ConnectionProfile(
            host=self.host,
            port=self.port,
            username=self.username,
            key=self.key,
            password=self.password,
        )


@dataclass(slots=True)
class ProfileLoadResult:
    """Outcome of loading connection settings for a command run."""

    profile: ConnectionProfile
    saved_args: dict[str, Any]
    fallback_source: str | None = None
    used_saved_args: bool = False


def load_connection_profile(profile: ConnectionProfile) -> ProfileLoadResult:
    """Return a ProfileLoadResult with the given profile (no config persistence)."""
    resolved = profile.resolved()
    return ProfileLoadResult(
        profile=resolved,
        saved_args={},
        fallback_source=None,
        used_saved_args=False,
    )


def complete_connection_profile(profile: ConnectionProfile, interactive: bool) -> ConnectionProfile | None:
    """Prompt for or validate remote identity fields when needed."""
    resolved = profile.resolved()

    if interactive and not resolved.host:
        prompted_host = Prompt.ask("Remote host (or localhost)", default="localhost").strip()
        resolved = ConnectionProfile(
            host=prompted_host,
            port=resolved.port,
            username=resolved.username,
            key=resolved.key,
            password=resolved.password,
        ).resolved()

    if resolved.mode == "remote" and interactive and not resolved.username:
        details = prompt_connection_details(resolved.host, resolved.port)
        return ConnectionProfile(
            host=details["host"],
            port=details["port"],
            username=details["username"],
            key=details["key_filename"],
            password=details["password"],
        ).resolved()

    if resolved.mode == "remote" and (not resolved.host or not resolved.username):
        return None

    return resolved


def build_connection(profile: ConnectionProfile, command_timeout: float | None = None):
    """Construct the concrete connection object for a normalized profile."""
    resolved = profile.resolved()
    if resolved.mode == "local":
        if command_timeout is not None:
            return LocalConnection(
                username=resolved.username,
                password=resolved.password,
                key_filename=resolved.key,
                command_timeout=command_timeout,
            )
        return LocalConnection(
            username=resolved.username,
            password=resolved.password,
            key_filename=resolved.key,
        )

    if command_timeout is not None:
        return SSHConnection(
            host=resolved.host,
            port=resolved.port,
            username=resolved.username,
            password=resolved.password,
            key_filename=resolved.key,
            command_timeout=command_timeout,
        )
    return SSHConnection(
        host=resolved.host,
        port=resolved.port,
        username=resolved.username,
        password=resolved.password,
        key_filename=resolved.key,
    )


@contextmanager
def managed_connection(connection) -> Iterator[Any]:
    """Ensure connect/disconnect lifecycle is consistent across commands."""
    if not connection.connect():
        raise ConnectionError("Failed to connect to remote host")
    try:
        yield connection
    finally:
        connection.disconnect()


def connection_args(profile: ConnectionProfile) -> dict[str, Any]:
    """Return config-safe connection args for persistence."""
    resolved = profile.resolved()
    # Normalize 'local' to 'localhost' for config persistence
    host = resolved.host
    if host.strip().lower() == "local":
        host = "localhost"
    return {
        "remote": host,
        "port": resolved.port,
        "username": resolved.username,
        "key": resolved.key,
    }


def connection_args_from_connection(connection: Any) -> dict[str, Any]:
    """Return config-safe connection args using the active connection state."""
    host = getattr(connection, "host", "")
    if str(host).strip().lower() == "local":
        host = "localhost"
    return {
        "remote": host,
        "port": getattr(connection, "port", DEFAULT_SSH_PORT),
        "username": getattr(connection, "username", ""),
        "key": getattr(connection, "key_filename", ""),
    }


def load_defaulted_value(current: Any, default: Any, saved_args: Mapping[str, Any], key: str) -> Any:
    """Return a saved value when the caller is still using its default."""
    if current == default and key in saved_args:
        return saved_args[key]
    return current


# ---------------------------------------------------------------------------
# Canonical shared resolvers
# ---------------------------------------------------------------------------

def resolve_connection_profile(
    profile: ConnectionProfile,
    interactive: bool = False,
) -> ConnectionProfile | None:
    """Resolve connection settings from CLI input and optional prompts."""
    result = load_connection_profile(profile)
    return complete_connection_profile(result.profile, interactive)