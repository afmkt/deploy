"""Shared CLI session helpers for connection-oriented commands."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

from .config import DeployConfig
from .local import LocalConnection
from .ssh import SSHConnection
from .target import resolve_target
from .utils import prompt_connection_details

DEFAULT_SSH_PORT = 22


@dataclass(slots=True)
class ConnectionProfile:
    """Normalized connection settings for a command run."""

    target: str = "remote"
    host: str = ""
    port: int = DEFAULT_SSH_PORT
    username: str = ""
    key: str = ""
    password: str | None = None

    def resolved(self) -> "ConnectionProfile":
        return ConnectionProfile(
            target=resolve_target(self.target, self.host),
            host=self.host,
            port=self.port,
            username=self.username,
            key=self.key,
            password=self.password,
        )


@dataclass(slots=True)
class ProfileLoadResult:
    """Outcome of loading connection settings from config and CLI input."""

    profile: ConnectionProfile
    saved_args: dict[str, Any]
    fallback_source: str | None = None
    used_saved_args: bool = False


def load_connection_profile(
    config: DeployConfig,
    section: str,
    profile: ConnectionProfile,
    *,
    use_config: bool,
    fallback_sources: Iterable[str] = (),
) -> ProfileLoadResult:
    """Load and normalize connection settings for a command.

    The command's own saved section is preferred. When requested, fallback
    sources can provide a complete remote SSH profile if the command-specific
    config is incomplete.
    """
    saved_args = config.load_args(section) if use_config else {}
    fallback_source = None

    if use_config and fallback_sources and _needs_complete_remote_profile(saved_args):
        for source in fallback_sources:
            candidate = config.load_args(source)
            if candidate.get("target") == "local":
                continue
            if _has_complete_remote_profile(candidate):
                saved_args = candidate
                fallback_source = source
                break

    target = profile.target
    if target == "remote" and saved_args.get("target") == "local":
        target = "local"

    loaded = ConnectionProfile(
        target=target,
        host=profile.host or str(saved_args.get("host", "")),
        port=profile.port if profile.port != DEFAULT_SSH_PORT else int(saved_args.get("port", DEFAULT_SSH_PORT)),
        username=profile.username or str(saved_args.get("username", "")),
        key=profile.key or str(saved_args.get("key", "")),
        password=profile.password,
    ).resolved()

    return ProfileLoadResult(
        profile=loaded,
        saved_args=saved_args,
        fallback_source=fallback_source,
        used_saved_args=bool(saved_args),
    )


def complete_connection_profile(profile: ConnectionProfile, interactive: bool) -> ConnectionProfile | None:
    """Prompt for or validate remote identity fields when needed."""
    resolved = profile.resolved()
    if resolved.target == "remote" and interactive and not resolved.host:
        details = prompt_connection_details()
        return ConnectionProfile(
            target=resolved.target,
            host=details["host"],
            port=details["port"],
            username=details["username"],
            key=details["key_filename"],
            password=details["password"],
        ).resolved()

    if resolved.target == "remote" and (not resolved.host or not resolved.username):
        return None

    return resolved


def build_connection(profile: ConnectionProfile, command_timeout: float | None = None):
    """Construct the concrete connection object for a normalized profile."""
    resolved = profile.resolved()
    if resolved.target == "local":
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
        raise ConnectionError("Failed to connect to deployment target")
    try:
        yield connection
    finally:
        connection.disconnect()


def connection_args(profile: ConnectionProfile) -> dict[str, Any]:
    """Return config-safe connection args for persistence."""
    resolved = profile.resolved()
    return {
        "host": resolved.host,
        "port": resolved.port,
        "username": resolved.username,
        "key": resolved.key,
        "target": resolved.target,
    }


def connection_args_from_connection(connection: Any) -> dict[str, Any]:
    """Return config-safe connection args using the active connection state."""
    return {
        "host": getattr(connection, "host", ""),
        "port": getattr(connection, "port", DEFAULT_SSH_PORT),
        "username": getattr(connection, "username", ""),
        "key": getattr(connection, "key_filename", ""),
        "target": "local" if getattr(connection, "is_local", False) else "remote",
    }


def load_defaulted_value(current: Any, default: Any, saved_args: Mapping[str, Any], key: str) -> Any:
    """Return a saved value when the caller is still using its default."""
    if current == default and key in saved_args:
        return saved_args[key]
    return current


def _has_complete_remote_profile(saved_args: Mapping[str, Any]) -> bool:
    return bool(saved_args.get("host") and saved_args.get("username") and saved_args.get("key"))


def _needs_complete_remote_profile(saved_args: Mapping[str, Any]) -> bool:
    return not _has_complete_remote_profile(saved_args)