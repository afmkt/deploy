import pytest
import json
import tempfile
from pathlib import Path
from deploy.config import DeployConfig


def test_config_save_and_load():
    """Test saving and loading configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save args
        args = {
            "host": "example.com",
            "port": 22,
            "username": "user",
            "key": "/path/to/key",
            "deploy_path": "~/.deploy/repos",
        }
        config.save_args(args, "push")
        
        # Load args
        loaded_args = config.load_args("push")
        assert loaded_args == args


def test_config_filters_password():
    """Test that password is filtered out when saving."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save args with password
        args = {
            "host": "example.com",
            "username": "user",
            "password": "secret123",
        }
        config.save_args(args, "push")
        
        # Load args
        loaded_args = config.load_args("push")
        assert "password" not in loaded_args
        assert loaded_args["host"] == "example.com"


def test_config_filters_none_values():
    """Test that None values are filtered out when saving."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save args with None values
        args = {
            "host": "example.com",
            "port": None,
            "username": "user",
            "key": None,
        }
        config.save_args(args, "push")
        
        # Load args
        loaded_args = config.load_args("push")
        assert "port" not in loaded_args
        assert "key" not in loaded_args
        assert loaded_args["host"] == "example.com"


def test_config_separate_commands():
    """Test that push and pull configs are stored separately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save push args
        push_args = {"host": "push.example.com", "username": "push_user"}
        config.save_args(push_args, "push")
        
        # Save pull args
        pull_args = {"host": "pull.example.com", "username": "pull_user"}
        config.save_args(pull_args, "pull")
        
        # Load args
        loaded_push = config.load_args("push")
        loaded_pull = config.load_args("pull")
        
        assert loaded_push["host"] == "push.example.com"
        assert loaded_pull["host"] == "pull.example.com"


def test_config_load_nonexistent():
    """Test loading config when file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Load args from nonexistent file
        loaded_args = config.load_args("push")
        assert loaded_args == {}


def test_config_clear_command():
    """Test clearing config for a specific command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save args for both commands
        config.save_args({"host": "push.example.com"}, "push")
        config.save_args({"host": "pull.example.com"}, "pull")
        
        # Clear push config
        config.clear_config("push")
        
        # Verify push is cleared but pull remains
        assert config.load_args("push") == {}
        assert config.load_args("pull") == {"host": "pull.example.com"}


def test_config_clear_all():
    """Test clearing all config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        # Save args for both commands
        config.save_args({"host": "push.example.com"}, "push")
        config.save_args({"host": "pull.example.com"}, "pull")
        
        # Clear all config
        config.clear_config()
        
        # Verify both are cleared
        assert config.load_args("push") == {}
        assert config.load_args("pull") == {}


def test_config_get_path():
    """Test getting config file path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".deploy"
        config = DeployConfig(config_dir=config_dir)
        
        path = config.get_config_path()
        assert path == config_dir / "config.json"


def test_config_default_directory():
    """Test that config uses current working directory by default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            config = DeployConfig()
            
            # Verify config directory is in current working directory
            expected_dir = Path(tmpdir) / ".deploy"
            assert config.get_config_path().resolve() == (expected_dir / "config.json").resolve()
            
            # Verify we can save and load
            config.save_args({"host": "test.com"}, "push")
            loaded = config.load_args("push")
            assert loaded == {"host": "test.com"}
        finally:
            os.chdir(original_cwd)
