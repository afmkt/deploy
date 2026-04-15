import pytest
from click.testing import CliRunner
from main import main
from main import proxy
from main import service
import main as main_module

def test_main_help():
    runner = CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert "Git SSH Deploy Tool" in result.output
    assert "--repo-path" in result.output
    assert "--target" in result.output


def test_service_deploy_local_auto_push_stays_local(monkeypatch):
    runner = CliRunner()
    nested = {}
    original_invoke = CliRunner.invoke

    class FakeConnection:
        is_local = True

        def __init__(self):
            self.host = "local"
            self.port = 0
            self.username = "tester"
            self.key_filename = None

        def connect(self):
            return True

        def disconnect(self):
            pass

    class FakeProxyManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def is_running(self):
            return True

        def get_configured_ingress_networks(self):
            return ["ingress"]

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def image_exists_remote(self, image):
            return False

        def ensure_service_dir(self, service_name):
            return True

        def upload_compose(self, service_name, compose_content):
            return True

        def upload_metadata(self, service_name, metadata_content):
            return True

        def compose_up(self, service_name):
            return True

        def get_status(self, service_name):
            return "running"

        def get_container_ip(self, service_name):
            return None

    class FakeResult:
        exit_code = 0

    def fake_build_connection_from_config(*args, **kwargs):
        return FakeConnection()

    def fake_invoke(self, command, args, **kwargs):
        if command is main_module.docker_push:
            nested["command"] = command
            nested["args"] = list(args)
            return FakeResult()
        return original_invoke(self, command, args, **kwargs)

    monkeypatch.setattr(main_module, "_build_connection_from_config", fake_build_connection_from_config)
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *args, **kwargs: True)
    monkeypatch.setattr("click.testing.CliRunner.invoke", fake_invoke)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    result = runner.invoke(service, [
        "deploy",
        "--target", "local",
        "--image", "repo/app:latest",
        "--domain", "app.example.com",
        "--no-use-config",
    ])

    assert result.exit_code == 0
    assert nested["command"] is main_module.docker_push
    assert "--host" in nested["args"]
    assert "localhost" in nested["args"]
    assert "--username" not in nested["args"]


def test_build_connection_uses_local_for_localhost_host():
    conn = main_module._build_connection(
        target="remote",
        host="localhost",
        port=22,
        username="",
        key="",
        password="",
    )
    assert getattr(conn, "is_local", False) is True


def test_proxy_status_reports_healthcheck_url(monkeypatch):
    runner = CliRunner()

    class FakeConnection:
        is_local = True
        host = "local"
        port = 0
        username = "tester"
        key_filename = None

        def connect(self):
            return True

        def disconnect(self):
            pass

    class FakeProxyManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self):
            return "running"

        def is_running(self):
            return True

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)

    result = runner.invoke(proxy, ["status", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0
    assert "Ingress proxy: running" in result.output
    assert "http://localhost/healthz" in result.output
