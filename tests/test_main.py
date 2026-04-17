from types import SimpleNamespace

from click.testing import CliRunner

from main import cli, image, main, proxy, service
import main as main_module


def test_root_help_shows_grouped_commands():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Git SSH Deploy Tool" in result.output
    assert "--non-interactive" in result.output
    assert "repo" in result.output
    assert "image" in result.output
    assert "proxy" in result.output
    assert "svc" in result.output


def test_repo_push_persists_only_after_success(monkeypatch):
    runner = CliRunner()
    persisted = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(
                    repo_path=kwargs["repo_path"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                ),
            )

    def fake_execute(context, console):
        return True

    def fake_persist(config, context):
        persisted["repo_path"] = context.repo_path
        persisted["deploy_path"] = context.deploy_path

    monkeypatch.setattr(main_module, "PushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_push", fake_execute)
    monkeypatch.setattr(main_module, "persist_push_resolution", fake_persist)

    result = runner.invoke(cli, [
        "--non-interactive",
        "repo",
        "push",
        "--path", "/tmp/deploy/repos",
        "--remote", "localhost",
    ])

    assert result.exit_code == 0
    assert persisted == {
        "repo_path": ".",
        "deploy_path": "/tmp/deploy/repos",
    }


def test_repo_push_does_not_persist_on_failure(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                used_saved_args=False,
                context=SimpleNamespace(
                    repo_path=kwargs["repo_path"],
                    deploy_path=kwargs["deploy_path"],
                    profile=kwargs["profile"],
                ),
            )

    monkeypatch.setattr(main_module, "PushArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_push", lambda *args, **kwargs: False)
    monkeypatch.setattr(main_module, "persist_push_resolution", lambda *args, **kwargs: persisted.__setitem__("called", True))

    result = runner.invoke(cli, [
        "--non-interactive",
        "repo",
        "push",
        "--path", "/tmp/deploy/repos",
        "--remote", "localhost",
    ])

    assert result.exit_code == 1
    assert persisted["called"] is False


def test_proxy_up_persists_networks(monkeypatch):
    runner = CliRunner()
    persisted = {}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(
                context=SimpleNamespace(
                    profile=kwargs["profile"],
                    networks=("ingress", "app-a"),
                    migrate_native_caddy=kwargs["migrate_native_caddy"],
                    interactive=kwargs["interactive"],
                )
            )

    def fake_execute(context, console, docker_push_command):
        return True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)

    monkeypatch.setattr(main_module, "ProxyUpArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_proxy_up", fake_execute)
    monkeypatch.setattr(
        "deploy.config.DeployConfig.save_args",
        lambda self, args, section: persisted.update({"args": args, "section": section}),
    )

    result = runner.invoke(cli, [
        "--non-interactive",
        "proxy",
        "up",
        "--remote", "localhost",
        "--network", "ingress",
        "--network", "app-a",
    ])

    assert result.exit_code == 0
    assert persisted["section"] == "proxy.up"
    assert persisted["args"]["remote"] == "localhost"
    assert persisted["args"]["network"] == ["ingress", "app-a"]


def test_service_init_uses_saved_image(monkeypatch):
    runner = CliRunner()
    called = {}

    class FakeResolver:
        def resolve(self, **kwargs):
            called["image"] = kwargs["image"]
            return SimpleNamespace(context=SimpleNamespace(
                image=kwargs["image"],
                domain=kwargs["domain"],
                service_name=kwargs["name"] or "api",
                port=kwargs["port"] or 8000,
                ingress_networks=tuple(kwargs["ingress_networks"]),
                global_ingress=kwargs["global_ingress"],
                path_prefix=kwargs["path_prefix"],
                internal=not bool(kwargs["domain"]),
            ))

    monkeypatch.setattr(main_module, "ServiceInitArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_init", lambda *args, **kwargs: True)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda self, section: {"image": "repo/app:latest"})
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)

    result = runner.invoke(service, ["init"])

    assert result.exit_code == 0
    assert called["image"] == "repo/app:latest"


def test_service_init_requires_image_in_non_interactive_mode():
    runner = CliRunner()
    result = runner.invoke(cli, ["--non-interactive", "svc", "init"])

    assert result.exit_code == 2
    assert "--image is required" in result.output


def test_service_init_no_domain_means_internal(monkeypatch):
    """Omitting --domain must make internal=True (derived, no --internal flag needed)."""
    runner = CliRunner()
    resolved = {}

    class FakeResolver:
        def resolve(self, **kwargs):
            resolved["domain"] = kwargs["domain"]
            resolved["internal_derived"] = not bool(kwargs["domain"])
            return SimpleNamespace(context=SimpleNamespace(
                image=kwargs["image"],
                domain=kwargs["domain"],
                service_name="mysvc",
                port=8000,
                ingress_networks=(),
                global_ingress=False,
                path_prefix=None,
                internal=not bool(kwargs["domain"]),
            ))

    monkeypatch.setattr(main_module, "ServiceInitArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_init", lambda *args, **kwargs: True)
    monkeypatch.setattr("deploy.config.DeployConfig.load_args", lambda self, section: {"image": "store:latest"})
    monkeypatch.setattr("deploy.config.DeployConfig.save_args", lambda *args, **kwargs: None)

    result = runner.invoke(service, ["init", "--name", "mysvc"])

    assert result.exit_code == 0
    assert resolved["domain"] is None
    assert resolved["internal_derived"] is True


def test_service_init_rejects_internal_flag():
    """--internal must no longer exist as a CLI option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["svc", "init", "--internal"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_service_up_uses_remote_flag(monkeypatch):
    runner = CliRunner()
    persisted = {"called": False}

    class FakeResolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, config, **kwargs):
            return SimpleNamespace(context=SimpleNamespace(service_name="myapp", profile=kwargs["profile"]))

    monkeypatch.setattr(main_module, "ServiceDeployArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_service_deploy", lambda *args, **kwargs: (True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)))
    monkeypatch.setattr(main_module, "persist_service_deploy_resolution", lambda *args, **kwargs: persisted.__setitem__("called", True))

    result = runner.invoke(service, [
        "up",
        "--remote", "localhost",
        "--username", "tester",
        "--no-use-config",
    ])

    assert result.exit_code == 0
    assert persisted["called"] is True


def test_image_build_uses_tag_and_build_command(monkeypatch):
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
                )
            )

    def fake_execute(context, console, *, push_command):
        calls["image"] = context.image
        calls["push_command"] = push_command
        return True, SimpleNamespace(host="localhost", port=22, username="tester", key_filename=None)

    monkeypatch.setattr(main_module, "ImageBuildArgumentResolver", FakeResolver)
    monkeypatch.setattr(main_module, "execute_image_build", fake_execute)
    monkeypatch.setattr(main_module, "persist_image_build_resolution", lambda *args, **kwargs: None)

    result = runner.invoke(image, [
        "build",
        "--tag", "repo/app:latest",
        "--path", "/tmp/deploy/repos",
        "--remote", "localhost",
        "--username", "tester",
        "--no-use-config",
    ])

    assert result.exit_code == 0
    assert calls["image"] == "repo/app:latest"
    assert calls["push_command"] is main
