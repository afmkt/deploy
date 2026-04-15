"""Shared ingress network configuration for proxy and service routing."""

from typing import Optional, Sequence

INGRESS_NETWORK = "ingress"


def normalize_ingress_networks(ingress_networks: Optional[Sequence[str]] = None) -> list[str]:
    """Normalize ingress network names from CLI/config input.

    Supports repeated values and comma-separated values. Empty tokens are ignored.
    """
    if not ingress_networks:
        return [INGRESS_NETWORK]

    normalized: list[str] = []
    for value in ingress_networks:
        for item in value.split(","):
            network = item.strip()
            if network and network not in normalized:
                normalized.append(network)

    return normalized or [INGRESS_NETWORK]
