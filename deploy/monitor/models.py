"""State models for the deploy monitor TUI."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass
class ServiceState:
    """Remote service status entry."""

    name: str
    status: str = "unknown"


@dataclass
class ResourceState:
    """Host resource summary."""

    load_avg: str = "n/a"
    memory: str = "n/a"
    disk: str = "n/a"
    docker_containers: int = 0
    docker_images: int = 0


@dataclass
class Snapshot:
    """Point-in-time monitor snapshot."""

    timestamp: datetime = field(default_factory=utc_now)
    connected: bool = False
    error: str = ""
    proxy_status: str = "unknown"
    services: list[ServiceState] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    resources: ResourceState = field(default_factory=ResourceState)


@dataclass
class ActionResult:
    """Action execution result for activity log and notifications."""

    ok: bool
    action: str
    message: str
    timestamp: datetime = field(default_factory=utc_now)
