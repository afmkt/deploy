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
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "push")
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


def test_service_deploy_remote_build_on_missing_image(monkeypatch):
    """When the image is missing and the user selects 'build', the remote build path is used."""
    runner = CliRunner()
    calls = {}

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
            pass

        def is_running(self):
            return True

        def get_configured_ingress_networks(self):
            return ["ingress"]

    class FakeServiceManager:
        def __init__(self, ssh):
            pass

        def image_exists_remote(self, image):
            return False

        def context_is_git_repo(self, context_path):
            calls["context_path"] = context_path
            return True

        def get_context_revision(self, context_path):
            return "abc123"

        def build_image_from_context(self, image, context_path):
            calls["build_image_from_context"] = (image, context_path)
            return True

        def read_service_metadata(self, service_name):
            return None

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

    class FakeGitRepository:
        def __init__(self, path):
            pass

        def validate(self):
            return True

        def get_repo_name(self):
            return "myrepo"

        def get_current_revision(self):
            return "abc123"

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr(main_module, "GitRepository", FakeGitRepository)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "build")
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    result = runner.invoke(service, [
        "deploy",
        "--target", "local",
        "--image", "repo/app:latest",
        "--domain", "app.example.com",
        "--deploy-path", "/tmp/deploy/repos",
        "--no-use-config",
    ])

    assert result.exit_code == 0
    assert "build_image_from_context" in calls
    assert calls["build_image_from_context"][0] == "repo/app:latest"
    assert calls["build_image_from_context"][1] == "/tmp/deploy/repos/myrepo"


def test_service_deploy_non_interactive_requires_image_resolution(monkeypatch):
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
            pass

        def is_running(self):
            return True

        def get_configured_ingress_networks(self):
            return ["ingress"]

    class FakeServiceManager:
        def __init__(self, ssh):
            pass

        def read_service_metadata(self, service_name):
            return None

        def image_exists_remote(self, image):
            return True

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    result = runner.invoke(service, [
        "deploy",
        "--target", "local",
        "--domain", "app.example.com",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert "Image is required in non-interactive mode" in result.output


def test_service_deploy_non_interactive_build_requires_deploy_path(monkeypatch):
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
            pass

        def is_running(self):
            return True

        def get_configured_ingress_networks(self):
            return ["ingress"]

    class FakeServiceManager:
        def __init__(self, ssh):
            pass

        def image_exists_remote(self, image):
            return False

        def read_service_metadata(self, service_name):
            return None

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    result = runner.invoke(service, [
        "deploy",
        "--target", "local",
        "--image", "repo/app:latest",
        "--domain", "app.example.com",
        "--no-use-config",
        "--no-interactive",
        "--missing-image-action", "build",
    ])

    assert result.exit_code == 1
    assert "Deploy path is required for remote build context in non-interactive mode" in result.output


def test_build_connection_uses_local_for_localhost_host():
    from deploy.session import build_connection, ConnectionProfile

    profile = ConnectionProfile(
        target="remote",
        host="localhost",
        port=22,
        username="",
        key="",
        password="",
    )
    conn = build_connection(profile)
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
    assert "Ingress proxy is running (running)" in result.output
    assert "http://localhost/healthz" in result.output


def test_proxy_status_reports_not_running_state(monkeypatch):
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
            return "exited"

        def is_running(self):
            return False

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)

    result = runner.invoke(proxy, ["status", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0
    assert "Ingress proxy is not running (status: exited)" in result.output
    assert "Run: deploy proxy up" in result.output


    def test_proxy_up_non_interactive_auto_migrates_native_caddy(monkeypatch):
        """--no-interactive: native Caddy migration proceeds automatically without a Confirm prompt."""
        runner = CliRunner()
        calls = {}

        class FakeConnection:
            is_local = True
            host = "localhost"
            port = 22
            username = "tester"
            key_filename = None

            def connect(self):
                return True

            def disconnect(self):
                pass

        class FakeProxyManager:
            def __init__(self, ssh):
                self.ssh = ssh

            def native_caddy_exists(self):
                return True

            def ensure_networks(self, networks):
                return True

            def proxy_image_exists_remote(self):
                return True

            def read_native_caddyfile(self):
                return "localhost:80 { root /* /var/www }"

            def get_native_caddyfile_path(self):
                return "/etc/caddy/Caddyfile"

            def native_config_uses_loopback_upstreams(self, content):
                return False

            def rewrite_native_caddyfile_for_bridge_mode(self, content):
                return content

            def write_bootstrap_caddyfile(self, content):
                calls["write_bootstrap_caddyfile"] = content
                return True

            def deploy_compose_file(self, networks):
                return True

            def stop_native_caddy(self):
                calls["stop_native_caddy"] = True
                return True

            def up(self):
                return True

            def get_status(self):
                return "running"

        class FakeServiceManager:
            def __init__(self, ssh):
                pass

            def reconcile_global_services(self, networks):
                return True

        def raise_if_confirm_called(*args, **kwargs):
            raise AssertionError("Confirm.ask must not be called in non-interactive mode")

        monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
        monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
        monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
        monkeypatch.setattr("rich.prompt.Confirm.ask", raise_if_confirm_called)
        monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
        monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

        result = runner.invoke(proxy, [
            "up",
            "--host", "localhost",
            "--username", "tester",
            "--no-use-config",
            "--no-interactive",
        ])

        assert result.exit_code == 0, result.output
        assert "stop_native_caddy" in calls
        assert "write_bootstrap_caddyfile" in calls


    def test_proxy_up_non_interactive_auto_pushes_proxy_image(monkeypatch):
        """--no-interactive: missing proxy image triggers auto docker-push without a Confirm prompt."""
        runner = CliRunner()
        nested = {}
        original_invoke = CliRunner.invoke

        class FakeConnection:
            is_local = True
            host = "localhost"
            port = 22
            username = "tester"
            key_filename = None

            def connect(self):
                return True

            def disconnect(self):
                pass

        class FakeProxyManager:
            def __init__(self, ssh):
                self.ssh = ssh

            def native_caddy_exists(self):
                return False

            def ensure_networks(self, networks):
                return True

            def proxy_image_exists_remote(self):
                return False

            def write_bootstrap_caddyfile(self, content):
                return True

            def deploy_compose_file(self, networks):
                return True

            def up(self):
                return True

            def get_status(self):
                return "running"

        class FakeServiceManager:
            def __init__(self, ssh):
                pass

            def reconcile_global_services(self, networks):
                return True

        class FakeResult:
            exit_code = 0

        def fake_invoke(self, command, args, **kwargs):
            if command is main_module.docker_push:
                nested["command"] = command
                nested["args"] = list(args)
                return FakeResult()
            return original_invoke(self, command, args, **kwargs)

        def raise_if_confirm_called(*args, **kwargs):
            raise AssertionError("Confirm.ask must not be called in non-interactive mode")

        monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
        monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
        monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
        monkeypatch.setattr("click.testing.CliRunner.invoke", fake_invoke)
        monkeypatch.setattr("rich.prompt.Confirm.ask", raise_if_confirm_called)
        monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
        monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

        result = runner.invoke(proxy, [
            "up",
            "--host", "localhost",
            "--username", "tester",
            "--no-use-config",
            "--no-interactive",
        ])

        assert result.exit_code == 0, result.output
        assert "command" in nested, "expected docker_push to be invoked"
        assert nested["command"] is main_module.docker_push
    assert "http://localhost/healthz" in result.output
