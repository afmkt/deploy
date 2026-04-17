import pytest
from click.testing import CliRunner
from pathlib import Path
from types import SimpleNamespace
from main import main
from main import proxy
from main import service
from main import image
import main as main_module


def _write_service_compose(image: str = "repo/app:latest") -> None:
    Path("docker-compose.yml").write_text(
        """version: \"3.8\"\n\nservices:\n  myapp:\n    image: """
        + image
        + """\n    container_name: myapp\n    expose:\n      - \"8000\"\n    networks:\n      - ingress\n    labels:\n      caddy: app.example.com\n      caddy.reverse_proxy: \"{{upstreams 8000}}\"\n      deploy.scope: single\n    restart: unless-stopped\n\nnetworks:\n  ingress:\n    external: true\n    name: ingress\n"""
    )


def test_main_help():
    runner = CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert "Git SSH Deploy Tool" in result.output
    assert "--repo-path" in result.output


def test_push_persists_args_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, *, repo_path, deploy_path, profile):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(repo_path=repo_path, deploy_path=deploy_path, profile=profile),
            )

    def fake_execute_push(context, console, *, dry_run=False):
        return True

    def fake_persist(config, context):
        persisted["repo_path"] = context.repo_path
        persisted["deploy_path"] = context.deploy_path

    monkeypatch.setattr(main_module, "PushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_push", fake_execute_push)
    monkeypatch.setattr(main_module, "persist_push_resolution", fake_persist)
    monkeypatch.setattr("deploy.config.DeployConfig.get_config_path", lambda self: ".deploy/config.json")

    result = runner.invoke(main, [
        "--repo-path", ".",
        "--deploy-path", "/tmp/deploy/repos",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 0
    assert persisted == {
        "repo_path": ".",
        "deploy_path": "/tmp/deploy/repos",
    }


def test_push_does_not_persist_when_execution_fails(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, *, repo_path, deploy_path, profile):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(repo_path=repo_path, deploy_path=deploy_path, profile=profile),
            )

    def fake_execute_push(context, console, *, dry_run=False):
        return False

    def fake_persist(config, context):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "PushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_push", fake_execute_push)
    monkeypatch.setattr(main_module, "persist_push_resolution", fake_persist)

    result = runner.invoke(main, [
        "--repo-path", ".",
        "--deploy-path", "/tmp/deploy/repos",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False


def test_pull_persists_args_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(
                    repo_path=kwargs["repo_path"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                    commit=kwargs["commit"],
                    sync_remote=kwargs["sync_remote"],
                    branch=kwargs["branch"],
                ),
            )

    def fake_execute_pull(context, console, *, dry_run=False):
        return True

    def fake_persist(config, context):
        persisted["repo_path"] = context.repo_path
        persisted["deploy_path"] = context.deploy_path

    monkeypatch.setattr(main_module, "PullArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_pull", fake_execute_pull)
    monkeypatch.setattr(main_module, "persist_pull_resolution", fake_persist)
    monkeypatch.setattr("deploy.config.DeployConfig.get_config_path", lambda self: ".deploy/config.json")

    result = runner.invoke(main_module.pull, [
        "--repo-path", ".",
        "--deploy-path", "/tmp/deploy/repos",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 0
    assert persisted == {
        "repo_path": ".",
        "deploy_path": "/tmp/deploy/repos",
    }


def test_pull_does_not_persist_when_execution_fails(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(
                    repo_path=kwargs["repo_path"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                    commit=kwargs["commit"],
                    sync_remote=kwargs["sync_remote"],
                    branch=kwargs["branch"],
                ),
            )

    def fake_execute_pull(context, console, *, dry_run=False):
        return False

    def fake_persist(config, context):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "PullArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_pull", fake_execute_pull)
    monkeypatch.setattr(main_module, "persist_pull_resolution", fake_persist)

    result = runner.invoke(main_module.pull, [
        "--repo-path", ".",
        "--deploy-path", "/tmp/deploy/repos",
        "--host", "localhost",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False
    

def test_proxy_up_persists_args_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    profile=kwargs["profile"],
                    networks=("ingress",),
                    migrate_native_caddy=kwargs["migrate_native_caddy"],
                    interactive=kwargs["interactive"],
                )
            )

    def fake_execute(context, console, docker_push_command):
        return True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)

    def fake_persist(config, connection):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "ProxyUpArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_proxy_up", fake_execute)
    monkeypatch.setattr(main_module, "persist_proxy_up_resolution", fake_persist)

    result = runner.invoke(proxy, [
        "up",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 0
    assert persisted["called"] is True


def test_proxy_up_does_not_persist_when_execution_fails(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    profile=kwargs["profile"],
                    networks=("ingress",),
                    migrate_native_caddy=kwargs["migrate_native_caddy"],
                    interactive=kwargs["interactive"],
                )
            )

    def fake_execute(context, console, docker_push_command):
        return False, None

    def fake_persist(config, connection):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "ProxyUpArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_proxy_up", fake_execute)
    monkeypatch.setattr(main_module, "persist_proxy_up_resolution", fake_persist)

    result = runner.invoke(proxy, [
        "up",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False


def test_service_deploy_persists_args_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    service_name="myapp",
                    profile=kwargs["profile"],
                )
            )

    def fake_execute(context, console):
        return True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)

    def fake_persist(config, connection):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "ServiceDeployArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_deploy", fake_execute)
    monkeypatch.setattr(main_module, "persist_service_deploy_resolution", fake_persist)

    result = runner.invoke(service, [
        "up",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
    ])

    assert result.exit_code == 0
    assert persisted["called"] is True


def test_service_deploy_does_not_persist_when_execution_fails(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    service_name="myapp",
                    profile=kwargs["profile"],
                )
            )

    def fake_execute(context, console):
        return False, None

    def fake_persist(config, connection):
        persisted["called"] = True

    monkeypatch.setattr(main_module, "ServiceDeployArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_deploy", fake_execute)
    monkeypatch.setattr(main_module, "persist_service_deploy_resolution", fake_persist)

    result = runner.invoke(service, [
        "up",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False


def test_service_init_writes_service_skill_file():
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(service, [
            "init",
            "--domain", "api.example.com",
            "--name", "api",
            "--port", "8000",
        ])

        assert result.exit_code == 0
        assert ".github/skills/deploy-service/SKILL.md" in result.output

        skill_path = Path(".github/skills/deploy-service/SKILL.md")
        assert skill_path.exists()
        skill_content = skill_path.read_text()
        assert "Service Deployment Skill: api" in skill_content
        assert "Domain/host: api.example.com" in skill_content
        assert "## Execution Contract" in skill_content
        assert "Persist on success" in skill_content
        assert "1. Created or updated artifacts" in result.output
        assert "2. Resolved arguments (value <- origin)" in result.output
        assert "3. Most likely customization points" in result.output
        assert "4. Most likely next command" in result.output


def test_service_init_summary_reports_argument_origins_for_defaults():
    runner = CliRunner()

    with runner.isolated_filesystem():
        Path("main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

        result = runner.invoke(service, [
            "init",
            "--internal",
        ])

        assert result.exit_code == 0
        assert "name:" in result.output
        assert "default (current directory name)" in result.output
        assert "domain:" not in result.output
        assert "port: 8000" in result.output
        assert "detected from main.py" in result.output
        assert "ingress_networks: ingress" in result.output
        assert "default (ingress)" in result.output
        assert "deploy service deploy -n" in result.output


def test_service_init_delegates_to_flow(monkeypatch):
    runner = CliRunner()
    called = {}

    class FakeResolver:
        def resolve(self, **kwargs):
            called["resolved"] = kwargs
            return SimpleNamespace(context=SimpleNamespace(service_name="api"))

    def fake_execute(context, console):
        called["executed_service"] = context.service_name
        return True

    monkeypatch.setattr(main_module, "ServiceInitArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_init", fake_execute)

    result = runner.invoke(service, [
        "init",
        "--domain", "api.example.com",
        "--name", "api",
        "--port", "8000",
    ])

    assert result.exit_code == 0
    assert called["resolved"]["domain"] == "api.example.com"
    assert called["executed_service"] == "api"


def test_service_init_exits_when_execution_fails(monkeypatch):
    runner = CliRunner()

    class FakeResolver:
        def resolve(self, **kwargs):
            return SimpleNamespace(context=SimpleNamespace(service_name="api"))

    def fake_execute(context, console):
        return False

    monkeypatch.setattr(main_module, "ServiceInitArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_init", fake_execute)

    result = runner.invoke(service, [
        "init",
        "--domain", "api.example.com",
    ])

    assert result.exit_code == 1


def test_service_deploy_local_auto_push_stays_local(monkeypatch):
    runner = CliRunner()
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

        def get_repo_details(self, service_name):
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

    monkeypatch.setattr("deploy.service_deploy_flow.build_connection", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr("deploy.service_deploy_flow.ProxyManager", FakeProxyManager)
    monkeypatch.setattr("deploy.service_deploy_flow.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    with runner.isolated_filesystem():
        _write_service_compose("repo/app:latest")

        result = runner.invoke(service, [
            "up",
            "--host", "localhost",
            "--no-use-config",
        ])

    assert result.exit_code == 1
    assert "Image 'repo/app:latest' not found on remote host" in result.output
    assert "Use: deploy image push repo/app:latest" in result.output
    assert "Or : deploy image build-remote" in result.output


def test_service_deploy_remote_build_on_missing_image(monkeypatch):
    """image build-remote delegates to the dedicated flow and persists args on success."""
    runner = CliRunner()
    calls = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    image=kwargs["image"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                    interactive=kwargs["interactive"],
                    use_config=False,
                )
            )

    def fake_execute(context, console, *, config, push_command):
        calls["image"] = context.image
        calls["deploy_path"] = context.deploy_path
        calls["push_command"] = push_command
        return True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)

    def fake_persist(config, profile):
        calls["persisted_host"] = profile.host

    monkeypatch.setattr(main_module, "ImageBuildRemoteArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_image_build_remote", fake_execute)
    monkeypatch.setattr(main_module, "persist_image_build_remote_resolution", fake_persist)

    result = runner.invoke(image, [
        "build-remote",
        "--image", "repo/app:latest",
        "--deploy-path", "/tmp/deploy/repos",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 0
    assert calls["image"] == "repo/app:latest"
    assert calls["deploy_path"] == "/tmp/deploy/repos"
    assert calls["push_command"] is main_module.main
    assert calls["persisted_host"] == "localhost"


def test_service_deploy_non_interactive_defaults_to_build_when_image_missing(monkeypatch):
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

        def get_repo_details(self, service_name):
            return None

    monkeypatch.setattr("deploy.service_deploy_flow.build_connection", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr("deploy.service_deploy_flow.ProxyManager", FakeProxyManager)
    monkeypatch.setattr("deploy.service_deploy_flow.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *args, **kwargs: {})

    with runner.isolated_filesystem():
        _write_service_compose("repo/app:latest")

        result = runner.invoke(service, [
            "up",
            "--host", "localhost",
            "--no-use-config",
        ])

    assert result.exit_code == 1
    assert "Image 'repo/app:latest' not found on remote host" in result.output
    assert "Use: deploy image push repo/app:latest" in result.output
    assert "Or : deploy image build-remote" in result.output


def test_service_deploy_non_interactive_build_requires_deploy_path(monkeypatch):
    runner = CliRunner()
    calls = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    image=kwargs["image"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                    interactive=kwargs["interactive"],
                    use_config=False,
                )
            )

    def fake_execute(context, console, *, config, push_command):
        calls["deploy_path"] = context.deploy_path
        calls["interactive"] = context.interactive
        return False, None

    monkeypatch.setattr(main_module, "ImageBuildRemoteArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_image_build_remote", fake_execute)

    result = runner.invoke(image, [
        "build-remote",
        "--image", "repo/app:latest",
        "--host", "localhost",
        "--username", "tester",
        "--no-use-config",
        "--no-interactive",
    ])

    assert result.exit_code == 1
    assert calls["deploy_path"] is None
    assert calls["interactive"] is False


def test_build_connection_uses_local_for_localhost_host():
    from deploy.session import build_connection, ConnectionProfile

    profile = ConnectionProfile(
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


    def test_service_deploy_resolves_domain_from_local_metadata(monkeypatch, tmp_path):
        runner = CliRunner()
        calls = {}

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

            def ensure_service_dir(self, service_name):
                return True

            def upload_compose(self, service_name, compose_content):
                calls["compose"] = compose_content
                return True

            def upload_metadata(self, service_name, metadata_content):
                calls["metadata"] = metadata_content
                return True

            def compose_up(self, service_name):
                return True

            def get_status(self, service_name):
                return "running"

            def get_container_ip(self, service_name):
                return None

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".deploy-service.json").write_text(
            '{\n  "domain": "x.com",\n  "image": "repo/app:latest"\n}\n'
        )

        monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
        monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
        monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
        monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
        monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

        result = runner.invoke(service, [
            "up",
            "--host", "localhost", "--no-use-config",
        ])

        assert result.exit_code == 0, result.output
        assert "Domain : x.com" in result.output
        assert "x.com" in calls["compose"]


    def test_service_deploy_non_interactive_requires_domain_when_unresolvable(monkeypatch, tmp_path):
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

        class FakeServiceManager:
            def __init__(self, ssh):
                pass

            def read_service_metadata(self, service_name):
                return None

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
        monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
        monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
        monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
        monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

        result = runner.invoke(service, [
            "up",
            "--host", "localhost", "--no-use-config",
            "--no-interactive",
        ])

        assert result.exit_code == 1
        assert "Domain is required in non-interactive mode" in result.output


    def test_service_deploy_resolves_image_from_deployed_container(monkeypatch, tmp_path):
        runner = CliRunner()
        calls = {}

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
                return {"domain": "x.com", "image": None}

            def get_deployed_image(self, service_name):
                return "repo/app:latest"

            def image_exists_remote(self, image):
                return True

            def ensure_service_dir(self, service_name):
                return True

            def upload_compose(self, service_name, compose_content):
                calls["compose"] = compose_content
                return True

            def upload_metadata(self, service_name, metadata_content):
                calls["metadata"] = metadata_content
                return True

            def compose_up(self, service_name):
                return True

            def get_status(self, service_name):
                return "running"

            def get_container_ip(self, service_name):
                return None

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
        monkeypatch.setattr(main_module, "ProxyManager", FakeProxyManager)
        monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
        monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
        monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

        result = runner.invoke(service, [
            "up",
            "--host", "localhost", "--no-use-config",
            "--no-interactive",
        ])

        assert result.exit_code == 0, result.output
        assert "image: repo/app:latest" in calls["compose"]
    assert "http://localhost/healthz" in result.output


def test_service_deploy_requires_local_compose_non_interactive(monkeypatch, tmp_path):
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

    class FakeServiceManager:
        def __init__(self, ssh):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("deploy.service_deploy_flow.build_connection", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr("deploy.service_deploy_flow.ProxyManager", FakeProxyManager)
    monkeypatch.setattr("deploy.service_deploy_flow.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, [
        "up",
        "--host", "localhost", "--no-use-config",
    ])

    assert result.exit_code == 1, result.output
    assert "docker-compose.yml is required" in result.output


def test_service_deploy_uses_local_compose_for_routing(monkeypatch, tmp_path):
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

        def get_routed_host(self, service_name):
            return "x.com"

        def image_exists_remote(self, image):
            return True

        def get_repo_details(self, service_name):
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

    monkeypatch.chdir(tmp_path)
    _write_service_compose("repo/app:latest")
    monkeypatch.setattr("deploy.service_deploy_flow.build_connection", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr("deploy.service_deploy_flow.ProxyManager", FakeProxyManager)
    monkeypatch.setattr("deploy.service_deploy_flow.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *a, **kw: None)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, [
        "up",
        "--host", "localhost", "--no-use-config",
    ])

    assert result.exit_code == 0, result.output
    assert "Current routed host: x.com" in result.output
    assert "Domain : app.example.com" in result.output


def test_service_status_shows_logs(monkeypatch):
    """service status displays recent container logs alongside the status."""
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

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self, service_name):
            return "running"

        def get_logs(self, service_name, lines=20):
            return "INFO: Application startup complete.\nINFO: Uvicorn running on http://0.0.0.0:8000\n"

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, ["status", "--name", "myapp", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0, result.output
    assert "myapp" in result.output
    assert "running" in result.output
    assert "Recent logs" in result.output
    assert "Application startup complete" in result.output


def test_service_status_restarting_shows_logs(monkeypatch):
    """service status shows logs for a restarting container to aid diagnosis."""
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

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self, service_name):
            return "restarting"

        def get_logs(self, service_name, lines=20):
            return "error: Failed to spawn: `uvicorn`\n  Caused by: No such file or directory (os error 2)\n"

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, ["status", "--name", "auth", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0, result.output
    assert "restarting" in result.output
    assert "Recent logs" in result.output
    assert "Failed to spawn" in result.output


def test_service_status_no_logs_skips_section(monkeypatch):
    """service status omits the log section when the container has no output."""
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

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self, service_name):
            return "running"

        def get_logs(self, service_name, lines=20):
            return ""

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, ["status", "--name", "myapp", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0, result.output
    assert "Recent logs" not in result.output


def test_service_status_warns_on_route_host_metadata_mismatch(monkeypatch):
    """service status warns when active routed host diverges from persisted metadata domain."""
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

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self, service_name):
            return "running"

        def get_routed_host(self, service_name):
            return "x.com"

        def read_service_metadata(self, service_name):
            return {"domain": "localhost", "port": 8000}

        def get_logs(self, service_name, lines=20):
            return ""

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, ["status", "--name", "auth", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0, result.output
    assert "Route host: x.com" in result.output
    assert "Metadata domain: localhost" in result.output
    assert 'Ingress access: curl -H "Host: x.com" http://localhost/<path>' in result.output
    assert "In-network access: http://auth:8000/<path>" in result.output
    assert "Routed host does not match persisted service domain metadata" in result.output


def test_service_status_localhost_http_only_message(monkeypatch):
    """service status makes localhost HTTP-only routing explicit."""
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

    class FakeServiceManager:
        def __init__(self, ssh):
            self.ssh = ssh

        def get_status(self, service_name):
            return "running"

        def get_routed_host(self, service_name):
            return "localhost"

        def get_routed_site_label(self, service_name):
            return "http://localhost"

        def read_service_metadata(self, service_name):
            return {"domain": "localhost", "port": 8000}

        def get_logs(self, service_name, lines=20):
            return ""

    monkeypatch.setattr(main_module, "_build_connection_from_config", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main_module, "ServiceManager", FakeServiceManager)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda *a, **kw: {})

    result = runner.invoke(service, ["status", "--name", "auth", "--host", "localhost", "--no-use-config"])

    assert result.exit_code == 0, result.output
    assert "Route host: localhost" in result.output
    assert "Ingress access: curl http://localhost/<path>" in result.output
    assert "Ingress protocol: HTTP only (no localhost TLS certificate required)" in result.output
