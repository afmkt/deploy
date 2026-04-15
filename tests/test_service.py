"""Tests for deploy.service helpers and ServiceManager."""

import textwrap
from pathlib import Path
import pytest

from deploy.service import (
    detect_fastapi_entrypoint,
    render_dockerfile,
    render_service_metadata,
    render_service_compose,
    ServiceManager,
)
from deploy.ingress import INGRESS_NETWORK


class DummySSH:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.executed = []

    def execute(self, cmd):
        self.executed.append(cmd)
        if self._responses:
            return self._responses.pop(0)
        return (0, "", "")


# ---------------------------------------------------------------------------
# detect_fastapi_entrypoint
# ---------------------------------------------------------------------------

def test_detect_entrypoint_main_py(tmp_path):
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    rel, app_str, port = detect_fastapi_entrypoint(tmp_path)
    assert rel == "main.py"
    assert "app" in app_str
    assert port == 8000


def test_detect_entrypoint_app_main(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("import fastapi\napp = fastapi.FastAPI()\n")
    rel, app_str, port = detect_fastapi_entrypoint(tmp_path)
    assert rel == "app/main.py"
    assert "app.main" in app_str


def test_detect_entrypoint_fallback(tmp_path):
    """When no recognized entrypoint exists, return generic fallback."""
    rel, app_str, port = detect_fastapi_entrypoint(tmp_path)
    assert rel == "main.py"
    assert app_str == "main:app"
    assert port == 8000


def test_detect_entrypoint_file_exists_but_no_fastapi(tmp_path):
    """File present but no FastAPI import → fall through to fallback."""
    (tmp_path / "main.py").write_text("print('hello')\n")
    rel, app_str, _ = detect_fastapi_entrypoint(tmp_path)
    assert rel == "main.py"
    assert app_str == "main:app"  # fallback


# ---------------------------------------------------------------------------
# render_dockerfile
# ---------------------------------------------------------------------------

def test_render_dockerfile_contains_port():
    df = render_dockerfile("main:app", 8080)
    assert "EXPOSE 8080" in df
    assert '"8080"' in df


def test_render_dockerfile_contains_app_str():
    df = render_dockerfile("app.main:app", 8000)
    assert '"app.main:app"' in df


def test_render_dockerfile_starts_with_from():
    df = render_dockerfile("main:app", 8000)
    assert df.startswith("FROM python:3.12-slim")


# ---------------------------------------------------------------------------
# render_service_compose
# ---------------------------------------------------------------------------

def test_render_service_compose_caddy_label():
    compose = render_service_compose("mysvc", "api.example.com", 8000, image="myimage:latest")
    assert "caddy: api.example.com" in compose


def test_render_service_compose_reverse_proxy_label():
    compose = render_service_compose("mysvc", "api.example.com", 8000, image="myimage:latest")
    assert "caddy.reverse_proxy" in compose
    assert "upstreams 8000" in compose


def test_render_service_compose_image_directive():
    compose = render_service_compose("mysvc", "api.example.com", 8000, image="myimage:latest")
    assert "image: myimage:latest" in compose
    assert "build:" not in compose


def test_render_service_compose_build_directive():
    compose = render_service_compose("mysvc", "api.example.com", 8000)
    assert "build: ." in compose
    assert "image:" not in compose


def test_render_service_compose_external_network():
    compose = render_service_compose("mysvc", "api.example.com", 8000)
    assert "external: true" in compose
    assert f"name: {INGRESS_NETWORK}" in compose
    assert "      - ingress" in compose


def test_render_service_compose_custom_ingress_network():
    compose = render_service_compose(
        "mysvc",
        "api.example.com",
        8000,
        ingress_networks=["app-alpha-net"],
    )
    assert "name: app-alpha-net" in compose
    assert "      - app-alpha-net" in compose


def test_render_service_compose_multiple_ingress_networks_global_scope():
    compose = render_service_compose(
        "mysvc",
        "api.example.com",
        8000,
        image="myimage:latest",
        ingress_networks=["ingress", "app-a"],
        exposure_scope="global",
    )
    assert "      - ingress" in compose
    assert "      - app-a" in compose
    assert "deploy.scope: global" in compose


def test_render_service_metadata_contains_scope_and_networks():
    metadata = render_service_metadata(
        "mysvc",
        "api.example.com",
        8000,
        image="myimage:latest",
        ingress_networks=["ingress", "app-a"],
        exposure_scope="global",
    )
    assert '"exposure_scope": "global"' in metadata
    assert '"ingress_networks": [' in metadata
    assert '"app-a"' in metadata


# ---------------------------------------------------------------------------
# ServiceManager.image_exists_remote
# ---------------------------------------------------------------------------

def test_image_exists_remote_true():
    ssh = DummySSH(responses=[(0, "", "")])
    assert ServiceManager(ssh).image_exists_remote("myimage:tag") is True


def test_image_exists_remote_false():
    ssh = DummySSH(responses=[(1, "", "No such image")])
    assert ServiceManager(ssh).image_exists_remote("myimage:tag") is False


# ---------------------------------------------------------------------------
# ServiceManager.ensure_service_dir
# ---------------------------------------------------------------------------

def test_ensure_service_dir_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ServiceManager(ssh).ensure_service_dir("mysvc")
    assert result is True
    assert "mkdir" in ssh.executed[0]
    assert "mysvc" in ssh.executed[0]


def test_ensure_service_dir_failure():
    ssh = DummySSH(responses=[(1, "", "permission denied")])
    result = ServiceManager(ssh).ensure_service_dir("mysvc")
    assert result is False


# ---------------------------------------------------------------------------
# ServiceManager.upload_compose
# ---------------------------------------------------------------------------

def test_upload_compose_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ServiceManager(ssh).upload_compose("mysvc", "version: '3.8'\n")
    assert result is True
    assert "/tmp/deploy/services/mysvc/docker-compose.yml" in ssh.executed[0]


def test_upload_compose_failure():
    ssh = DummySSH(responses=[(1, "", "disk full")])
    result = ServiceManager(ssh).upload_compose("mysvc", "content")
    assert result is False


# ---------------------------------------------------------------------------
# ServiceManager.compose_up
# ---------------------------------------------------------------------------

def test_compose_up_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ServiceManager(ssh).compose_up("mysvc")
    assert result is True
    cmd = ssh.executed[0]
    assert "up" in cmd
    assert "--pull never" in cmd or "pull never" in cmd
    assert "mysvc" in cmd


def test_compose_up_failure():
    ssh = DummySSH(responses=[(1, "", "error")])
    result = ServiceManager(ssh).compose_up("mysvc")
    assert result is False


# ---------------------------------------------------------------------------
# ServiceManager.compose_down
# ---------------------------------------------------------------------------

def test_compose_down_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ServiceManager(ssh).compose_down("mysvc")
    assert result is True
    assert "down" in ssh.executed[0]


def test_compose_down_failure():
    ssh = DummySSH(responses=[(1, "", "error")])
    result = ServiceManager(ssh).compose_down("mysvc")
    assert result is False


# ---------------------------------------------------------------------------
# ServiceManager.get_status / get_container_ip
# ---------------------------------------------------------------------------

def test_get_status_running():
    ssh = DummySSH(responses=[(0, "running\n", "")])
    assert ServiceManager(ssh).get_status("mysvc") == "running"


def test_get_status_not_found():
    ssh = DummySSH(responses=[(1, "", "No such object")])
    assert ServiceManager(ssh).get_status("mysvc") is None


def test_get_deployed_image_found():
    ssh = DummySSH(responses=[(0, "repo/app:latest\n", "")])
    assert ServiceManager(ssh).get_deployed_image("mysvc") == "repo/app:latest"


def test_get_deployed_image_not_found():
    ssh = DummySSH(responses=[(1, "", "No such object")])
    assert ServiceManager(ssh).get_deployed_image("mysvc") is None


def test_get_container_ip_found():
    ssh = DummySSH(responses=[(0, "172.18.0.5\n", "")])
    assert ServiceManager(ssh).get_container_ip("mysvc") == "172.18.0.5"


def test_get_container_ip_not_found():
    ssh = DummySSH(responses=[(1, "", "No such object")])
    assert ServiceManager(ssh).get_container_ip("mysvc") is None


def test_restart_success():
    ssh = DummySSH(responses=[(0, "", "")])
    assert ServiceManager(ssh).restart("mysvc") is True
    assert "docker restart" in ssh.executed[0]


def test_restart_failure():
    ssh = DummySSH(responses=[(1, "", "error")])
    assert ServiceManager(ssh).restart("mysvc") is False


def test_get_logs():
    ssh = DummySSH(responses=[(0, "hello\n", "")])
    logs = ServiceManager(ssh).get_logs("mysvc", lines=15)
    assert logs == "hello\n"
    assert "--tail 15" in ssh.executed[0]


def test_list_services_success():
    ssh = DummySSH(responses=[(0, "api\nworker\n", "")])
    names = ServiceManager(ssh).list_services()
    assert names == ["api", "worker"]


def test_reconcile_global_services_updates_compose_and_restarts():
    metadata = render_service_metadata(
        "api",
        "api.example.com",
        8000,
        image="repo/api:latest",
        ingress_networks=["ingress"],
        exposure_scope="global",
    )
    ssh = DummySSH(
        responses=[
            (0, "api\nworker\n", ""),
            (0, metadata, ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, '{"service_name":"worker","domain":"worker.example.com","port":9000,"image":"repo/worker:latest","ingress_networks":["ingress"],"exposure_scope":"single"}', ""),
        ]
    )
    mgr = ServiceManager(ssh)
    assert mgr.reconcile_global_services(["ingress", "app-a"]) is True
    assert any("deploy.scope: global" in cmd for cmd in ssh.executed)
    assert any("app-a" in cmd and "docker-compose.yml" in cmd for cmd in ssh.executed)
    assert any("docker compose" in cmd and "up -d" in cmd for cmd in ssh.executed)


def test_list_services_failure():
    ssh = DummySSH(responses=[(1, "", "permission denied")])
    names = ServiceManager(ssh).list_services()
    assert names == []


# ---------------------------------------------------------------------------
# ServiceManager context checks and build
# ---------------------------------------------------------------------------

def test_context_is_git_repo_true():
    ssh = DummySSH(responses=[(0, "", "")])
    assert ServiceManager(ssh).context_is_git_repo("/tmp/deploy/repos/myrepo") is True


def test_context_is_git_repo_false():
    ssh = DummySSH(responses=[(1, "", "not a git repo")])
    assert ServiceManager(ssh).context_is_git_repo("/tmp/deploy/repos/myrepo") is False


def test_get_context_revision_success():
    ssh = DummySSH(responses=[(0, "abc123\n", "")])
    assert ServiceManager(ssh).get_context_revision("/tmp/deploy/repos/myrepo") == "abc123"


def test_get_context_revision_failure():
    ssh = DummySSH(responses=[(1, "", "bad revision")])
    assert ServiceManager(ssh).get_context_revision("/tmp/deploy/repos/myrepo") is None


def test_build_image_from_context_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ServiceManager(ssh).build_image_from_context("myimage:tag", "/tmp/deploy/repos/myrepo")
    assert result is True
    cmd = ssh.executed[0]
    assert "docker build" in cmd
    assert "myimage:tag" in cmd
    assert "/tmp/deploy/repos/myrepo" in cmd


def test_build_image_from_context_failure():
    ssh = DummySSH(responses=[(1, "", "no such file: Dockerfile")])
    result = ServiceManager(ssh).build_image_from_context("myimage:tag", "/tmp/deploy/repos/myrepo")
    assert result is False
