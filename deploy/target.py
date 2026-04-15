"""Shared helpers for resolving and presenting deployment targets."""

from __future__ import annotations


LOCAL_HOST_ALIASES = {"local", "localhost", "127.0.0.1", "::1", "[::1]"}


def is_local_host(host: str | None) -> bool:
    """Return True when a host value should map to the local machine."""
    return bool(host and host.strip().lower() in LOCAL_HOST_ALIASES)


def resolve_target(target: str, host: str | None) -> str:
    """Resolve target mode, allowing localhost-style hosts to imply local mode."""
    if is_local_host(host):
        return "local"
    return target


def is_local_connection(connection) -> bool:
    """Return True when a connection targets the local machine."""
    return bool(getattr(connection, "is_local", False))


def needs_remote_identity(connection) -> bool:
    """Return True when a remote target is missing required identity fields."""
    return not is_local_connection(connection) and (
        not getattr(connection, "host", None) or not getattr(connection, "username", None)
    )


def display_target(connection) -> str:
    """Return a concise label for an active deployment target."""
    if is_local_connection(connection):
        return "local machine"
    return f"{connection.username}@{connection.host}:{connection.port}"


def import_source_label(connection) -> str:
    """Return a short label suitable for imported config source naming."""
    if is_local_connection(connection):
        return "local"
    return getattr(connection, "host", "remote") or "remote"


def proxy_healthcheck_url(connection) -> str:
    """Return the user-facing proxy health endpoint URL for a connection."""
    host = "localhost" if is_local_connection(connection) else (getattr(connection, "host", None) or "<host>")
    return f"http://{host}/healthz"


def construct_repo_url(repo_path: str, connection) -> str:
    """Return a repository URL suitable for the active connection transport."""
    if is_local_connection(connection):
        return repo_path
    return f"ssh://{connection.username}@{connection.host}:{connection.port}{repo_path}"


def docker_push_args_for_connection(image: str, connection) -> list[str]:
    """Build docker-push CLI arguments for the given target connection."""
    args = [
        "--image",
        image,
        "--no-interactive",
        "--host",
        "localhost" if is_local_connection(connection) else connection.host,
    ]
    if not is_local_connection(connection):
        args.extend(["--port", str(connection.port), "--username", connection.username])
    if getattr(connection, "key_filename", None):
        args.extend(["--key", connection.key_filename])
    return args


def push_args_for_connection(repo_path: str, deploy_path: str, connection) -> list[str]:
    """Build push CLI arguments for the given target connection."""
    args = [
        "--repo-path",
        repo_path,
        "--deploy-path",
        deploy_path,
        "--no-interactive",
        "--host",
        "localhost" if is_local_connection(connection) else connection.host,
    ]
    if not is_local_connection(connection):
        args.extend(["--port", str(connection.port), "--username", connection.username])
    if getattr(connection, "key_filename", None):
        args.extend(["--key", connection.key_filename])
    return args