"""Configuration management for Git SSH Deploy Tool."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class DeployConfig:
    """Manages deployment configuration and saves latest arguments."""

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize the config manager.
        
        Args:
            config_dir: Optional custom config directory. If not provided,
                       uses .deploy in the current working directory.
        """
        if config_dir is not None:
            self._config_dir = config_dir
        else:
            self._config_dir = Path.cwd() / ".deploy"
        self._config_file = self._config_dir / "config.json"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        """Ensure the config directory exists."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

    def save_args(self, args: Dict[str, Any], command: str = "push"):
        """Save the latest arguments to config file.

        Args:
            args: Dictionary of argument names and values
            command: The command name (push or pull)
        """
        config = self.load_config()
        
        # Filter out None values and password for security
        filtered_args = {
            k: v for k, v in args.items() 
            if v is not None and k != "password"
        }
        
        config[command] = filtered_args
        
        try:
            with open(self._config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            # Silently fail if we can't save config
            pass

    def load_config(self) -> Dict[str, Any]:
        """Load the entire config file.

        Returns:
            Dictionary containing all saved configurations
        """
        if not self._config_file.exists():
            return {}
        
        try:
            with open(self._config_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}

    def load_args(self, command: str = "push") -> Dict[str, Any]:
        """Load saved arguments for a specific command.

        Args:
            command: The command name (push or pull)

        Returns:
            Dictionary of saved arguments for the command
        """
        config = self.load_config()
        return config.get(command, {})

    def get_config_path(self) -> Path:
        """Get the path to the config file.

        Returns:
            Path to the config file
        """
        return self._config_file

    def clear_config(self, command: Optional[str] = None):
        """Clear saved configuration.

        Args:
            command: If specified, clear only this command's config. 
                    Otherwise, clear all config.
        """
        if command:
            config = self.load_config()
            if command in config:
                del config[command]
                try:
                    with open(self._config_file, "w") as f:
                        json.dump(config, f, indent=2)
                except Exception:
                    pass
        else:
            try:
                if self._config_file.exists():
                    self._config_file.unlink()
            except Exception:
                pass
