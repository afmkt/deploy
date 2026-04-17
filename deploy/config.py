"""Configuration management for Git SSH Deploy Tool."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Schema: declares the allowed keys for each group.subcommand section.
# Unknown keys in the config file are an error.
# ---------------------------------------------------------------------------

_CONNECTION_KEYS: frozenset[str] = frozenset({"remote", "port", "username", "key"})

CONFIG_SCHEMA: dict[str, dict[str, frozenset[str]]] = {
    "repo": {
        "push": _CONNECTION_KEYS | {"path"},
        "pull": _CONNECTION_KEYS | {"path"},
    },
    "image": {
        "push": _CONNECTION_KEYS,
        "build": _CONNECTION_KEYS | {"path"},
    },
    "proxy": {
        "up": _CONNECTION_KEYS | {"network"},
        "status": _CONNECTION_KEYS,
        "down": _CONNECTION_KEYS,
        "logs": _CONNECTION_KEYS,
    },
    "svc": {
        "init": frozenset({"image", "domain", "name", "port", "network", "global", "path_prefix", "internal"}),
        "up": _CONNECTION_KEYS,
        "status": _CONNECTION_KEYS,
        "down": _CONNECTION_KEYS,
    },
    "monitor": {
        "_": _CONNECTION_KEYS,
    },
}

# Fields whose string values should have ~ expanded on load.
_PATH_FIELDS: frozenset[str] = frozenset({"key", "path"})


class ConfigValidationError(Exception):
    """Raised when config.yml contains unknown or structurally invalid keys."""


class DeployConfig:
    """Manages deployment configuration in YAML format.

    Configuration is stored as a nested YAML file at .deploy/config.yml,
    organised by top-level command group and subcommand:

        repo:
          push:
            remote: myhost.example.com
            username: deploy
            key: ~/.ssh/id_ed25519
            path: ~/.deploy/repos

    Unknown keys are rejected on validation. Path-like values (``key``,
    ``path``) have ``~`` expanded when loaded.
    """

    CONFIG_FILE = "config.yml"

    def __init__(self, config_dir: Path | None = None):
        if config_dir is not None:
            self._config_dir = config_dir
        else:
            self._config_dir = Path.cwd() / ".deploy"
        self._config_file = self._config_dir / self.CONFIG_FILE
        self._config_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_raw(self) -> dict[str, Any]:
        """Return the raw YAML data without any transformation."""
        if not self._config_file.exists():
            return {}
        with open(self._config_file) as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _expand_section(section: dict[str, Any]) -> dict[str, Any]:
        """Expand ~ in path-like field values within a section dict."""
        return {
            k: (os.path.expanduser(str(v)) if k in _PATH_FIELDS and isinstance(v, str) else v)
            for k, v in section.items()
        }

    def _write(self, data: dict[str, Any]) -> None:
        self._config_file.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self) -> dict[str, Any]:
        """Load the full config with path expansion applied to all sections."""
        self.validate()
        raw = self._load_raw()
        result: dict[str, Any] = {}
        for group, subcommands in raw.items():
            if isinstance(subcommands, dict):
                result[group] = {
                    cmd: (self._expand_section(args) if isinstance(args, dict) else args)
                    for cmd, args in subcommands.items()
                }
            else:
                result[group] = subcommands
        return result

    def load_args(self, section: str) -> dict[str, Any]:
        """Load saved args for a *group.subcommand* section key (e.g. ``"repo.push"``).

        Returns an empty dict when the section does not exist.
        """
        parts = section.split(".", 1)
        if len(parts) != 2:
            return {}
        group, subcommand = parts
        return self.load_config().get(group, {}).get(subcommand, {})

    def save_args(self, args: dict[str, Any], section: str) -> None:
        """Save resolved args for a *group.subcommand* section key.

        ``None`` values, ``"password"`` keys, and keys not in the schema
        for this section are silently dropped.
        """
        parts = section.split(".", 1)
        if len(parts) != 2:
            return
        group, subcommand = parts

        allowed = CONFIG_SCHEMA.get(group, {}).get(subcommand)

        filtered: dict[str, Any] = {}
        for k, v in args.items():
            if v is None or k == "password":
                continue
            if allowed is not None and k not in allowed:
                continue
            filtered[k] = v

        raw = self._load_raw()
        raw.setdefault(group, {})[subcommand] = filtered
        self._write(raw)

    def validate(self) -> None:
        """Validate the config file against the schema.

        Raises :exc:`ConfigValidationError` for any unknown group, subcommand,
        or key.
        """
        raw = self._load_raw()
        for group, subcommands in raw.items():
            if not isinstance(subcommands, dict):
                raise ConfigValidationError(f"Config section '{group}' must be a mapping")
            group_schema = CONFIG_SCHEMA.get(group)
            if group_schema is None:
                raise ConfigValidationError(f"Unknown config group: '{group}'")
            for cmd, args in subcommands.items():
                if not isinstance(args, dict):
                    raise ConfigValidationError(
                        f"Config section '{group}.{cmd}' must be a mapping"
                    )
                allowed = group_schema.get(cmd)
                if allowed is None:
                    raise ConfigValidationError(
                        f"Unknown config subcommand: '{group}.{cmd}'"
                    )
                unknown = set(args) - allowed
                if unknown:
                    raise ConfigValidationError(
                        f"Unknown keys in '{group}.{cmd}': {', '.join(sorted(unknown))}"
                    )

    def get_config_path(self) -> Path:
        """Return the path to the config file."""
        return self._config_file

    def clear_config(self, section: str | None = None) -> None:
        """Remove config data.

        ``section`` may be a ``"group.subcommand"`` key to remove one section,
        a plain group name to remove all subcommands under that group, or
        ``None`` to delete the entire config file.
        """
        if section is None:
            if self._config_file.exists():
                self._config_file.unlink()
            return

        raw = self._load_raw()
        parts = section.split(".", 1)
        if len(parts) == 2:
            group, subcommand = parts
            raw.get(group, {}).pop(subcommand, None)
        else:
            raw.pop(section, None)
        self._write(raw)
