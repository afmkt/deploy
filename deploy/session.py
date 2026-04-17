"""Shared CLI session helpers for connection-oriented commands."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping
from rich.prompt import Prompt

from .config import DeployConfig
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
            if is_local_host(_saved_host(candidate)):
                continue
            if _has_complete_remote_profile(candidate):
                saved_args = candidate
                fallback_source = source
                break

    saved_host = _saved_host(saved_args)

    loaded = ConnectionProfile(
        host=profile.host or saved_host,
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
    return {
        "remote": resolved.host,
        "port": resolved.port,
        "username": resolved.username,
        "key": resolved.key,
    }


def connection_args_from_connection(connection: Any) -> dict[str, Any]:
    """Return config-safe connection args using the active connection state."""
    return {
        "remote": getattr(connection, "host", ""),
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

ALL_FALLBACK_SOURCES: tuple[str, ...] = (
    "repo.push", "repo.pull", "image.push", "image.build",
    "proxy.up", "svc.up", "monitor._",
)


def resolve_connection_profile(
    config: "DeployConfig",
    section: str,
    profile: ConnectionProfile,
    *,
    use_config: bool,
    interactive: bool = False,
) -> ConnectionProfile | None:
    """Canonical connection resolution shared by all commands.

    Always applies the full cross-command fallback list so saved settings from
    any command are available to every other command.
    """
    result = load_connection_profile(
        config,
        section,
        profile,
        use_config=use_config,
        fallback_sources=ALL_FALLBACK_SOURCES,
    )
    return complete_connection_profile(result.profile, interactive)


def resolve_path_arg(
    cli_value: str,
    default: str,
    saved_args: Mapping[str, Any],
    key: str,
) -> str:
    """Resolve a path argument using CLI value, saved config, or default.

    Returns the CLI value when it differs from the default; otherwise falls
    back to the saved config value if present.
    """
    return load_defaulted_value(cli_value, default, saved_args, key)


def _has_complete_remote_profile(saved_args: Mapping[str, Any]) -> bool:
    host = _saved_host(saved_args)
    return bool(host and not is_local_host(host) and saved_args.get("username") and saved_args.get("key"))


def _needs_complete_remote_profile(saved_args: Mapping[str, Any]) -> bool:
    host = _saved_host(saved_args)
    if not host:
        return True
    if is_local_host(host):
        return False
    return not _has_complete_remote_profile(saved_args)


def _saved_host(saved_args: Mapping[str, Any]) -> str:
    host = str(saved_args.get("remote", ""))
    if host:
        return host
    if str(saved_args.get("target", "")).strip().lower() == "local":
        return "localhost"
    return ""