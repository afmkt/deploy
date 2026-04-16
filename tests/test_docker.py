"""Tests for DockerManager and docker-push CLI command."""

import subprocess
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
from click.testing import CliRunner

from deploy.docker import DockerManager, _safe_image_filename
from main import docker_push
import main as main_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DummySSH:
    def __init__(self, responses: dict | None = None):
        """
        responses maps substrings of commands to (exit_code, stdout, stderr) tuples.
        Default: all succeed.
        """
        self.commands: list[str] = []
        self.username = "user"
        self.host = "host"
        self.port = 22
        self.client = MagicMock()
        self._responses = responses or {}

    def execute(self, command: str) -> tuple[int, str, str]:
        self.commands.append(command)
        for key, response in self._responses.items():
            if key in command:
                return response
        return 0, "", ""


# ---------------------------------------------------------------------------
# _safe_image_filename
# ---------------------------------------------------------------------------

def test_safe_image_filename_simple():
    assert _safe_image_filename("nginx:latest") == "nginx_latest.tar"


def test_safe_image_filename_with_registry():
    assert _safe_image_filename("registry.example.com/org/app:v1.0") == \
        "registry.example.com_org_app_v1.0.tar"


# ---------------------------------------------------------------------------
# DockerManager.is_docker_installed
# ---------------------------------------------------------------------------

def test_is_docker_installed_true():
    ssh = DummySSH({"docker version": (0, "2.27.0\nOK", "")})
    mgr = DockerManager(ssh)
    assert mgr.is_docker_installed() is True


def test_is_docker_installed_false():
    ssh = DummySSH({"docker version": (1, "", "command not found")})
    mgr = DockerManager(ssh)
    assert mgr.is_docker_installed() is False


# ---------------------------------------------------------------------------
# DockerManager.detect_remote_arch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uname,expected_platform", [
    ("x86_64", "linux/amd64"),
    ("aarch64", "linux/arm64"),
    ("arm64", "linux/arm64"),
    ("armv7l", "linux/arm/v7"),
    ("armv6l", "linux/arm/v6"),
    ("i686", "linux/386"),
    ("s390x", "linux/s390x"),
    ("ppc64le", "linux/ppc64le"),
])
def test_detect_remote_arch(uname, expected_platform):
    ssh = DummySSH({"uname -m": (0, uname + "\n", "")})
    mgr = DockerManager(ssh)
    assert mgr.detect_remote_arch() == expected_platform


def test_detect_remote_arch_unknown_defaults_to_amd64():
    ssh = DummySSH({"uname -m": (0, "mips64\n", "")})
    mgr = DockerManager(ssh)
    assert mgr.detect_remote_arch() == "linux/amd64"


def test_detect_remote_arch_failure_returns_none():
    ssh = DummySSH({"uname -m": (1, "", "error")})
    mgr = DockerManager(ssh)
    assert mgr.detect_remote_arch() is None


# ---------------------------------------------------------------------------
# DockerManager.detect_os
# ---------------------------------------------------------------------------

def test_detect_os_ubuntu():
    ssh = DummySSH({"cat /etc/os-release": (0, 'ID=ubuntu\nVERSION_CODENAME=jammy\n', "")})
    assert DockerManager(ssh).detect_os() == "debian"


def test_detect_os_rhel():
    ssh = DummySSH({"cat /etc/os-release": (0, 'ID=rhel\n', "")})
    assert DockerManager(ssh).detect_os() == "rhel"


def test_detect_os_alpine():
    ssh = DummySSH({"cat /etc/os-release": (0, 'ID=alpine\n', "")})
    assert DockerManager(ssh).detect_os() == "alpine"


def test_detect_os_unknown():
    ssh = DummySSH({"cat /etc/os-release": (0, 'ID=arch\n', "")})
    assert DockerManager(ssh).detect_os() is None


# ---------------------------------------------------------------------------
# DockerManager.install_docker
# ---------------------------------------------------------------------------

def test_install_docker_already_installed():
    ssh = DummySSH({"docker version": (0, "2.27\nOK", "")})
    mgr = DockerManager(ssh)
    assert mgr.install_docker() is True
    # No install commands should have been issued
    assert not any("apt-get" in c or "yum" in c or "apk" in c for c in ssh.commands)


def test_install_docker_unsupported_os():
    ssh = DummySSH({
        "docker version": (1, "", "not found"),
        "cat /etc/os-release": (0, "ID=arch\n", ""),
    })
    assert DockerManager(ssh).install_docker() is False


def test_install_docker_os_detection_fails():
    ssh = DummySSH({
        "docker version": (1, "", "not found"),
        "cat /etc/os-release": (1, "", ""),
    })
    assert DockerManager(ssh).install_docker() is False


# ---------------------------------------------------------------------------
# DockerManager.load_image
# ---------------------------------------------------------------------------

def test_load_image_success():
    ssh = DummySSH({"docker load": (0, "Loaded image: nginx:latest\n", "")})
    assert DockerManager(ssh).load_image("/tmp/nginx_latest.tar") is True


def test_load_image_failure():
    ssh = DummySSH({"docker load": (1, "", "Error: no such file")})
    assert DockerManager(ssh).load_image("/tmp/nginx_latest.tar") is False


def test_load_image_quotes_path():
    ssh = DummySSH()
    DockerManager(ssh).load_image("/tmp/my image.tar")
    cmd = ssh.commands[-1]
    assert "'/tmp/my image.tar'" in cmd


def test_load_image_retags_when_saved_by_id():
    """When docker load reports 'Loaded image ID:', re-tag with original name."""
    loaded_id = "sha256:abc123def456"
    ssh = DummySSH({
        "docker load": (0, f"Loaded image ID: {loaded_id}\n", ""),
        "docker tag": (0, "", ""),
    })
    mgr = DockerManager(ssh)
    assert mgr.load_image("/tmp/nginx_latest.tar", "nginx:latest") is True
    assert any("docker tag" in c for c in ssh.commands)
    assert any("nginx:latest" in c for c in ssh.commands)


def test_load_image_no_retag_when_no_image_tag():
    """When no image_tag is supplied, skip the docker tag step."""
    loaded_id = "sha256:abc123def456"
    ssh = DummySSH({"docker load": (0, f"Loaded image ID: {loaded_id}\n", "")})
    mgr = DockerManager(ssh)
    assert mgr.load_image("/tmp/nginx_latest.tar") is True
    assert not any("docker tag" in c for c in ssh.commands)


def test_load_image_no_retag_when_name_already_present():
    """When docker load reports 'Loaded image: name:tag', no re-tag needed."""
    ssh = DummySSH({"docker load": (0, "Loaded image: nginx:latest\n", "")})
    mgr = DockerManager(ssh)
    assert mgr.load_image("/tmp/nginx_latest.tar", "nginx:latest") is True
    assert not any("docker tag" in c for c in ssh.commands)


# ---------------------------------------------------------------------------
# DockerManager.cleanup_remote
# ---------------------------------------------------------------------------

def test_cleanup_remote_issues_rm():
    ssh = DummySSH()
    DockerManager(ssh).cleanup_remote("/tmp/nginx_latest.tar")
    assert any("rm -f" in c for c in ssh.commands)


def test_cleanup_remote_quotes_path():
    ssh = DummySSH()
    DockerManager(ssh).cleanup_remote("/tmp/tricky path; evil")
    cmd = [c for c in ssh.commands if "rm -f" in c][0]
    assert "'/tmp/tricky path; evil'" in cmd


# ---------------------------------------------------------------------------
# DockerManager.get_local_image_id
# ---------------------------------------------------------------------------

def test_get_local_image_id_success(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "sha256:abc123def456\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.get_local_image_id("nginx:latest") == "sha256:abc123def456"


def test_get_local_image_id_failure(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.get_local_image_id("nginx:latest") is None


def test_get_local_image_id_docker_missing(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    assert mgr.get_local_image_id("nginx:latest") is None


# ---------------------------------------------------------------------------
# DockerManager.pull_image (local subprocess)
# ---------------------------------------------------------------------------

def test_pull_image_success(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 0
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.pull_image("nginx:latest", "linux/amd64") is True


def test_pull_image_failure(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "manifest not found"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.pull_image("nginx:latest", "linux/amd64") is False


def test_pull_image_docker_missing(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)

    def raise_fnf(*a, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    assert mgr.pull_image("nginx:latest", "linux/amd64") is False


# ---------------------------------------------------------------------------
# DockerManager.save_image (local subprocess)
# ---------------------------------------------------------------------------

def test_save_image_success(monkeypatch, tmp_path):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    tar_path = str(tmp_path / "nginx_latest.tar")
    # Create a fake file so stat() works
    (tmp_path / "nginx_latest.tar").write_bytes(b"\x00" * 1024 * 1024)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "sha256:abc123ef\n"  # returned by docker inspect
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.save_image("nginx:latest", tar_path, "linux/amd64") is True


def test_save_image_uses_platform_when_provided(monkeypatch, tmp_path):
    """docker save should prefer an explicit platform when one is provided."""
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    tar_path = str(tmp_path / "nginx_latest.tar")
    (tmp_path / "nginx_latest.tar").write_bytes(b"\x00" * 512)
    captured = []
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "sha256:deadbeef\n"

    def capture_run(cmd, **kw):
        captured.append(cmd)
        return mock_result

    monkeypatch.setattr(subprocess, "run", capture_run)
    mgr.save_image("nginx:latest", tar_path, "linux/amd64")
    save_cmd = [c for c in captured if "save" in c][0]
    assert save_cmd == [
        "docker",
        "save",
        "--platform",
        "linux/amd64",
        "nginx:latest",
        "-o",
        tar_path,
    ]


def test_save_image_falls_back_to_image_id_when_platform_save_fails(monkeypatch, tmp_path):
    """Older Docker versions may not support --platform for docker save."""
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    tar_path = str(tmp_path / "nginx_latest.tar")
    (tmp_path / "nginx_latest.tar").write_bytes(b"\x00" * 512)
    captured = []

    def capture_run(cmd, **kw):
        captured.append(cmd)
        result = MagicMock()
        if cmd[:4] == ["docker", "save", "--platform", "linux/amd64"]:
            result.returncode = 1
            result.stderr = "unknown flag: --platform"
            result.stdout = ""
            return result

        result.returncode = 0
        result.stdout = "sha256:deadbeef\n"
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", capture_run)
    assert mgr.save_image("nginx:latest", tar_path, "linux/amd64") is True
    assert captured[0] == ["docker", "inspect", "--format", "{{.Id}}", "nginx:latest"]
    assert captured[1] == ["docker", "save", "--platform", "linux/amd64", "nginx:latest", "-o", tar_path]
    assert captured[2] == ["docker", "save", "sha256:deadbeef", "-o", tar_path]


def test_save_image_failure(monkeypatch, tmp_path):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    def fail_run(cmd, **kw):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "image not found"
        return result

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert mgr.save_image("nginx:latest", str(tmp_path / "x.tar"), "linux/amd64") is False


# ---------------------------------------------------------------------------
# DockerManager.registry_login
# ---------------------------------------------------------------------------

def test_registry_login_success(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 0
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.registry_login("user", "pass", "nginx:latest") is True


def test_registry_login_uses_private_registry(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    captured = []
    mock_result = MagicMock()
    mock_result.returncode = 0

    def capture_run(cmd, **kw):
        captured.append(cmd)
        return mock_result

    monkeypatch.setattr(subprocess, "run", capture_run)
    mgr.registry_login("user", "pass", "registry.example.com/org/app:v1")
    assert "registry.example.com" in captured[0]


def test_registry_login_failure(monkeypatch):
    ssh = DummySSH()
    mgr = DockerManager(ssh)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "unauthorized"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    assert mgr.registry_login("user", "wrong", "nginx:latest") is False


# ---------------------------------------------------------------------------
# DockerManager.transfer_tarball (SFTP)
# ---------------------------------------------------------------------------

def test_transfer_tarball_success(tmp_path):
    local_tar = tmp_path / "nginx_latest.tar"
    local_tar.write_bytes(b"\x00" * 512)

    mock_sftp = MagicMock()
    mock_client = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp

    ssh = DummySSH()
    ssh.client = mock_client

    mgr = DockerManager(ssh)
    assert mgr.transfer_tarball(str(local_tar), "/tmp/nginx_latest.tar") is True
    mock_sftp.put.assert_called_once_with(str(local_tar), "/tmp/nginx_latest.tar")
    mock_sftp.close.assert_called_once()


def test_transfer_tarball_failure(tmp_path):
    local_tar = tmp_path / "nginx_latest.tar"
    local_tar.write_bytes(b"\x00" * 512)

    mock_client = MagicMock()
    mock_client.open_sftp.side_effect = Exception("SFTP not available")

    ssh = DummySSH()
    ssh.client = mock_client

    mgr = DockerManager(ssh)
    assert mgr.transfer_tarball(str(local_tar), "/tmp/nginx_latest.tar") is False


# ---------------------------------------------------------------------------
# CLI: docker-push --help
# ---------------------------------------------------------------------------

def test_docker_push_help():
    runner = CliRunner()
    result = runner.invoke(docker_push, ["--help"])
    assert result.exit_code == 0
    assert "--image" in result.output
    assert "--platform" in result.output
    assert "--registry-username" in result.output


# ---------------------------------------------------------------------------
# CLI: docker-push --dry-run
# ---------------------------------------------------------------------------

def test_docker_push_dry_run(monkeypatch):
    runner = CliRunner()

    def fake_connect(self):
        self.client = MagicMock()
        return True

    def fake_disconnect(self):
        pass

    from deploy import ssh as ssh_module

    monkeypatch.setattr(ssh_module.SSHConnection, "connect", fake_connect)
    monkeypatch.setattr(ssh_module.SSHConnection, "disconnect", fake_disconnect)

    with patch("deploy.docker.DockerManager.is_docker_installed", return_value=True), \
         patch("deploy.docker.DockerManager.get_docker_version", return_value="24.0"), \
         patch("deploy.docker.DockerManager.detect_remote_arch", return_value="linux/amd64"):

        result = runner.invoke(docker_push, [
            "--image", "nginx:latest",
            "--host", "example.com",
            "--username", "alice",
            "--no-interactive",
            "--dry-run",
        ])

    assert result.exit_code == 0
    assert "Dry run" in result.output or "linux/amd64" in result.output


def test_docker_push_use_config_falls_back_to_push_profile(monkeypatch):
    runner = CliRunner()
    captured = {}

    class FakeSSHConnection:
        def __init__(self, host, port=22, username=None, password=None, key_filename=None):
            captured["host"] = host
            captured["port"] = port
            captured["username"] = username
            captured["password"] = password
            captured["key_filename"] = key_filename
            self.client = MagicMock()

        def connect(self):
            return True

        def disconnect(self):
            pass

        def execute(self, command):
            return 0, "", ""

    def fake_load_args(self, command="push"):
        if command == "docker-push":
            return {"host": "example.com", "port": 22, "username": "alice"}
        if command == "push":
            return {
                "host": "47.100.30.18",
                "port": 22,
                "username": "root",
                "key": "/Users/michael/.ssh/id_rsa",
            }
        return {}

    monkeypatch.setattr("deploy.session.SSHConnection", FakeSSHConnection)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", fake_load_args)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.get_config_path", lambda self: ".deploy/config.json")

    with patch("deploy.docker.DockerManager.is_docker_installed", return_value=True), \
         patch("deploy.docker.DockerManager.get_docker_version", return_value="24.0"), \
         patch("deploy.docker.DockerManager.detect_remote_arch", return_value="linux/amd64"):
        result = runner.invoke(docker_push, [
            "--image", "nginx:latest",
            "--use-config",
            "--no-interactive",
            "--dry-run",
        ])

    assert result.exit_code == 0
    assert captured == {
        "host": "47.100.30.18",
        "port": 22,
        "username": "root",
        "password": None,
        "key_filename": "/Users/michael/.ssh/id_rsa",
    }


def test_docker_push_persists_args_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(image=kwargs["image"], profile=kwargs["profile"]),
                used_saved_args=False,
            )

    def fake_execute(context, console, *, dry_run=False):
        return True

    def fake_persist(config, context):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "DockerPushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_docker_push", fake_execute)
    monkeypatch.setattr(main_module, "persist_docker_push_resolution", fake_persist)
    monkeypatch.setattr("deploy.config.DeployConfig.get_config_path", lambda self: ".deploy/config.json")

    result = runner.invoke(docker_push, [
        "--image", "nginx:latest",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 0
    assert persisted["called"] is True


def test_docker_push_does_not_persist_when_execution_fails(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(image=kwargs["image"], profile=kwargs["profile"]),
                used_saved_args=False,
            )

    def fake_execute(context, console, *, dry_run=False):
        return False

    def fake_persist(config, context):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "DockerPushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_docker_push", fake_execute)
    monkeypatch.setattr(main_module, "persist_docker_push_resolution", fake_persist)

    result = runner.invoke(docker_push, [
        "--image", "nginx:latest",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False
