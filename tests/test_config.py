from pathlib import Path

import pytest

from deploy.config import ConfigValidationError, DeployConfig


def test_save_and_load_yaml_section(tmp_path):
    config = DeployConfig(config_dir=tmp_path)

    config.save_args(
        {
            "remote": "example.com",
            "username": "alice",
            "key": "~/.ssh/id_ed25519",
            "path": "~/deploy/repos",
            "password": "secret",
        },
        "repo.push",
    )

    loaded = config.load_args("repo.push")
    raw = config.get_config_path().read_text()

    assert "repo:" in raw
    assert "push:" in raw
    assert "password" not in raw
    assert loaded["remote"] == "example.com"
    assert loaded["username"] == "alice"
    assert loaded["key"] == str(Path("~/.ssh/id_ed25519").expanduser())
    assert loaded["path"] == str(Path("~/deploy/repos").expanduser())


def test_multiple_sections_are_isolated(tmp_path):
    config = DeployConfig(config_dir=tmp_path)

    config.save_args({"remote": "push.example.com", "path": "/push"}, "repo.push")
    config.save_args({"remote": "pull.example.com", "path": "/pull"}, "repo.pull")

    assert config.load_args("repo.push") == {
        "remote": "push.example.com",
        "path": "/push",
    }
    assert config.load_args("repo.pull") == {
        "remote": "pull.example.com",
        "path": "/pull",
    }


def test_unknown_keys_raise_validation_error(tmp_path):
    config = DeployConfig(config_dir=tmp_path)
    config.get_config_path().write_text(
        "repo:\n  push:\n    remote: example.com\n    bad_key: nope\n"
    )

    with pytest.raises(ConfigValidationError):
        config.load_config()


def test_clear_specific_section_preserves_others(tmp_path):
    config = DeployConfig(config_dir=tmp_path)

    config.save_args({"remote": "push.example.com"}, "repo.push")
    config.save_args({"remote": "pull.example.com"}, "repo.pull")
    config.clear_config("repo.push")

    assert config.load_args("repo.push") == {}
    assert config.load_args("repo.pull") == {"remote": "pull.example.com"}
