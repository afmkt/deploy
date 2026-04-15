"""Tests for deploy.proxy.ProxyManager."""

import pytest
from deploy.proxy import (
    ProxyManager,
    normalize_ingress_networks,
    render_bootstrap_caddyfile,
    render_proxy_compose,
    PROXY_HOST_GATEWAY_NAME,
    PROXY_IMAGE,
    INGRESS_NETWORK,
    PROXY_CONTAINER,
    PROXY_COMPOSE_REMOTE,
    PROXY_BOOTSTRAP_CADDYFILE_REMOTE,
    PROXY_AUTOSAVE_CADDYFILE_REMOTE,
)


class DummySSH:
    """Minimal SSH stand-in that records executed commands."""

    def __init__(self, responses=None):
        # responses: list of (exit_code, stdout, stderr) in call order;
        # anything not covered returns (0, "", "")
        self._responses = list(responses or [])
        self.executed = []

    def execute(self, cmd):
        self.executed.append(cmd)
        if self._responses:
            return self._responses.pop(0)
        return (0, "", "")


# ---------------------------------------------------------------------------
# network normalization / rendering
# ---------------------------------------------------------------------------

def test_normalize_ingress_networks_default():
    assert normalize_ingress_networks() == [INGRESS_NETWORK]


def test_normalize_ingress_networks_dedup_and_split_csv():
    networks = normalize_ingress_networks(["ingress, app-a", "app-b", "app-a"])
    assert networks == ["ingress", "app-a", "app-b"]


def test_render_proxy_compose_multiple_networks():
    compose = render_proxy_compose(["ingress", "app-a"])
    assert "CADDY_INGRESS_NETWORKS=ingress,app-a" in compose
    assert "networks:\n      - ingress\n      - app-a" in compose
    assert "  ingress:\n    external: true\n    name: ingress" in compose
    assert "  app-a:\n    external: true\n    name: app-a" in compose


def test_get_configured_ingress_networks_reads_compose_env():
    ssh = DummySSH(responses=[(0, 'services:\n  caddy-proxy:\n    environment:\n      - CADDY_INGRESS_NETWORKS=ingress,app-a\n', "")])
    mgr = ProxyManager(ssh)
    assert mgr.get_configured_ingress_networks() == ["ingress", "app-a"]


# ---------------------------------------------------------------------------
# network_exists
# ---------------------------------------------------------------------------

def test_network_exists_true():
    ssh = DummySSH(responses=[(0, "yes\n", "")])
    mgr = ProxyManager(ssh)
    assert mgr.network_exists() is True


def test_network_exists_false():
    ssh = DummySSH(responses=[(0, "no\n", "")])
    mgr = ProxyManager(ssh)
    assert mgr.network_exists() is False


# ---------------------------------------------------------------------------
# ensure_network
# ---------------------------------------------------------------------------

def test_ensure_network_already_exists(capsys):
    # network_exists → "yes", no create call needed
    ssh = DummySSH(responses=[(0, "yes\n", "")])
    mgr = ProxyManager(ssh)
    result = mgr.ensure_network()
    assert result is True
    assert len(ssh.executed) == 1


def test_ensure_network_creates_when_missing():
    # first call: network_exists → "no"; second call: docker network create → 0
    ssh = DummySSH(responses=[(0, "no\n", ""), (0, "ingress-id\n", "")])
    mgr = ProxyManager(ssh)
    result = mgr.ensure_network()
    assert result is True
    assert len(ssh.executed) == 2
    assert "network create" in ssh.executed[1]


def test_ensure_network_create_fails():
    ssh = DummySSH(responses=[(0, "no\n", ""), (1, "", "permission denied")])
    mgr = ProxyManager(ssh)
    result = mgr.ensure_network()
    assert result is False


def test_ensure_networks_multiple_names():
    # ingress exists, app-a missing then created
    ssh = DummySSH(
        responses=[
            (0, "yes\n", ""),
            (0, "no\n", ""),
            (0, "app-a-id\n", ""),
        ]
    )
    mgr = ProxyManager(ssh)
    assert mgr.ensure_networks(["ingress", "app-a"]) is True
    assert any("network create" in cmd and "app-a" in cmd for cmd in ssh.executed)


# ---------------------------------------------------------------------------
# proxy_image_exists_remote
# ---------------------------------------------------------------------------

def test_proxy_image_exists_remote_true():
    ssh = DummySSH(responses=[(0, "", "")])
    assert ProxyManager(ssh).proxy_image_exists_remote() is True


def test_proxy_image_exists_remote_false():
    ssh = DummySSH(responses=[(1, "", "Error")])
    assert ProxyManager(ssh).proxy_image_exists_remote() is False


# ---------------------------------------------------------------------------
# deploy_compose_file
# ---------------------------------------------------------------------------

def test_deploy_compose_file_success():
    # mkdir -p → 0,  cat heredoc → 0
    ssh = DummySSH(responses=[(0, "", ""), (0, "", "")])
    mgr = ProxyManager(ssh)
    result = mgr.deploy_compose_file()
    assert result is True
    assert len(ssh.executed) == 2
    assert "mkdir" in ssh.executed[0]
    assert PROXY_COMPOSE_REMOTE in ssh.executed[1]


def test_deploy_compose_file_with_multiple_networks_renders_env():
    ssh = DummySSH(responses=[(0, "", ""), (0, "", "")])
    mgr = ProxyManager(ssh)
    result = mgr.deploy_compose_file(["ingress", "app-a"])
    assert result is True
    assert "CADDY_INGRESS_NETWORKS=ingress,app-a" in ssh.executed[1]


def test_deploy_compose_file_mkdir_fails():
    ssh = DummySSH(responses=[(1, "", "permission denied")])
    mgr = ProxyManager(ssh)
    result = mgr.deploy_compose_file()
    assert result is False


def test_deploy_compose_file_write_fails():
    ssh = DummySSH(responses=[(0, "", ""), (1, "", "disk full")])
    mgr = ProxyManager(ssh)
    result = mgr.deploy_compose_file()
    assert result is False


# ---------------------------------------------------------------------------
# is_running / get_status
# ---------------------------------------------------------------------------

def test_is_running_true():
    ssh = DummySSH(responses=[(0, "true\n", "")])
    assert ProxyManager(ssh).is_running() is True


def test_is_running_false_state():
    ssh = DummySSH(responses=[(0, "false\n", "")])
    assert ProxyManager(ssh).is_running() is False


def test_is_running_container_missing():
    ssh = DummySSH(responses=[(1, "", "No such object")])
    assert ProxyManager(ssh).is_running() is False


def test_get_status_running():
    ssh = DummySSH(responses=[(0, "running\n", "")])
    assert ProxyManager(ssh).get_status() == "running"


def test_get_status_exited():
    ssh = DummySSH(responses=[(0, "exited\n", "")])
    assert ProxyManager(ssh).get_status() == "exited"


def test_get_status_not_found():
    ssh = DummySSH(responses=[(1, "", "No such object")])
    assert ProxyManager(ssh).get_status() is None


# ---------------------------------------------------------------------------
# up / down
# ---------------------------------------------------------------------------

def test_up_success():
    # compose -f ... up -d --pull never → 0
    ssh = DummySSH(responses=[(0, "", "")])
    result = ProxyManager(ssh).up()
    assert result is True
    cmd = ssh.executed[0]
    assert "--pull never" in cmd or "pull never" in cmd
    assert "up" in cmd


def test_up_failure():
    ssh = DummySSH(responses=[(1, "", "error")])
    result = ProxyManager(ssh).up()
    assert result is False


def test_down_success():
    ssh = DummySSH(responses=[(0, "", "")])
    result = ProxyManager(ssh).down()
    assert result is True
    assert "down" in ssh.executed[0]


def test_down_failure():
    ssh = DummySSH(responses=[(1, "", "error")])
    result = ProxyManager(ssh).down()
    assert result is False


# ---------------------------------------------------------------------------
# get_proxy_logs
# ---------------------------------------------------------------------------

def test_get_proxy_logs_returns_output():
    ssh = DummySSH(responses=[(0, "log line\n", "")])
    logs = ProxyManager(ssh).get_proxy_logs(lines=10)
    assert logs.strip() == "log line"
    assert "--tail 10" in ssh.executed[0] or "--tail" in ssh.executed[0]


def test_get_proxy_logs_empty_on_failure():
    ssh = DummySSH(responses=[(1, "", "not found")])
    assert ProxyManager(ssh).get_proxy_logs() == ""


def test_read_remote_file_success():
    ssh = DummySSH(responses=[(0, "hello\n", "")])
    assert ProxyManager(ssh).read_remote_file("/tmp/foo") == "hello\n"


def test_read_remote_file_missing_returns_none():
    ssh = DummySSH(responses=[(1, "", "")])
    assert ProxyManager(ssh).read_remote_file("/tmp/foo") is None


def test_get_generated_caddyfile_success():
    ssh = DummySSH(responses=[(0, "example.com {\n}\n", "")])
    content = ProxyManager(ssh).get_generated_caddyfile()
    assert "example.com" in content
    assert PROXY_AUTOSAVE_CADDYFILE_REMOTE in ssh.executed[0]


def test_get_native_caddy_status_returns_output():
    ssh = DummySSH(responses=[(3, "status output\n", "")])
    assert ProxyManager(ssh).get_native_caddy_status() == "status output\n"


def test_get_native_caddy_journal_returns_output():
    ssh = DummySSH(responses=[(0, "journal output\n", "")])
    assert ProxyManager(ssh).get_native_caddy_journal(lines=10) == "journal output\n"


# ---------------------------------------------------------------------------
# native Caddy migration helpers
# ---------------------------------------------------------------------------

def test_native_caddy_exists_when_systemd_unit_exists():
    ssh = DummySSH(responses=[(0, "", "")])
    assert ProxyManager(ssh).native_caddy_exists() is True


def test_native_caddy_exists_false_when_all_checks_fail():
    ssh = DummySSH(responses=[(1, "", ""), (1, "", ""), (1, "", "")])
    assert ProxyManager(ssh).native_caddy_exists() is False


def test_read_native_caddyfile_prefers_etc_path():
    ssh = DummySSH(responses=[(0, "exists\n", ""), (0, "example.com {\n}", "")])
    content = ProxyManager(ssh).read_native_caddyfile()
    assert "example.com" in content
    assert "/etc/caddy/Caddyfile" in ssh.executed[0]


def test_read_native_caddyfile_fallback_usr_local_path():
    ssh = DummySSH(responses=[(1, "", ""), (0, "exists\n", ""), (0, "example.org {\n}", "")])
    content = ProxyManager(ssh).read_native_caddyfile()
    assert "example.org" in content
    assert "/usr/local/etc/caddy/Caddyfile" in ssh.executed[1]


def test_read_native_caddyfile_missing_returns_none():
    ssh = DummySSH(responses=[(1, "", ""), (1, "", ""), (1, "", ""), (0, "", "")])
    assert ProxyManager(ssh).read_native_caddyfile() is None


def test_get_native_caddyfile_path_returns_detected_path():
    ssh = DummySSH(responses=[(1, "", ""), (1, "", ""), (0, "exists\n", "")])
    assert ProxyManager(ssh).get_native_caddyfile_path() == "/etc/caddy/caddy.conf"


def test_write_bootstrap_caddyfile_success():
    ssh = DummySSH(responses=[(0, "", ""), (0, "no\n", ""), (0, "", "")])
    ok = ProxyManager(ssh).write_bootstrap_caddyfile("example.com {\n}\n")
    assert ok is True
    assert "mkdir -p" in ssh.executed[0]
    assert "if [ -d" in ssh.executed[1]
    assert PROXY_BOOTSTRAP_CADDYFILE_REMOTE in ssh.executed[2]
    assert "deploy proxy is healthy" in ssh.executed[2]


def test_write_bootstrap_caddyfile_recovers_when_path_is_directory():
    ssh = DummySSH(responses=[(0, "", ""), (0, "yes\n", ""), (0, "", ""), (0, "", "")])
    ok = ProxyManager(ssh).write_bootstrap_caddyfile("example.com {\n}\n")

    assert ok is True
    assert "if [ -d" in ssh.executed[1]
    assert "rm -rf" in ssh.executed[2]
    assert PROXY_BOOTSTRAP_CADDYFILE_REMOTE in ssh.executed[2]
    assert PROXY_BOOTSTRAP_CADDYFILE_REMOTE in ssh.executed[3]


def test_render_bootstrap_caddyfile_default_fallback():
    content = render_bootstrap_caddyfile("")
    assert 'http://localhost {' not in content
    assert ':80 {' in content
    assert 'handle_path /healthz {' in content
    assert 'respond "deploy proxy is healthy" 200' in content
    assert 'respond "deploy proxy is running but no routes match this host" 404' in content


def test_render_bootstrap_caddyfile_appends_existing_content():
    content = render_bootstrap_caddyfile("example.com {\n    reverse_proxy localhost:3000\n}\n")
    assert 'http://localhost {' not in content
    assert 'example.com {' in content
def test_rewrite_native_caddyfile_for_container_localhost():
    mgr = ProxyManager(DummySSH())
    original = "example.com {\n    reverse_proxy localhost:3000\n}\n"
    rewritten = mgr.rewrite_native_caddyfile_for_container(original)
    assert f"reverse_proxy {PROXY_HOST_GATEWAY_NAME}:3000" in rewritten


def test_rewrite_native_caddyfile_for_container_127001():
    mgr = ProxyManager(DummySSH())
    original = "example.com {\n    reverse_proxy 127.0.0.1:8080\n}\n"
    rewritten = mgr.rewrite_native_caddyfile_for_container(original)
    assert f"reverse_proxy {PROXY_HOST_GATEWAY_NAME}:8080" in rewritten


def test_rewrite_native_caddyfile_for_container_preserves_non_loopback():
    mgr = ProxyManager(DummySSH())
    original = "example.com {\n    reverse_proxy 10.0.0.15:9000\n}\n"
    rewritten = mgr.rewrite_native_caddyfile_for_container(original)
    assert rewritten == original


def test_rewrite_native_caddyfile_for_bridge_mode_uses_gateway_ip():
    ssh = DummySSH(responses=[(0, "172.18.0.1\n", "")])
    mgr = ProxyManager(ssh)
    original = "example.com {\n    reverse_proxy localhost:9000\n}\n"
    rewritten = mgr.rewrite_native_caddyfile_for_bridge_mode(original)
    assert "reverse_proxy 172.18.0.1:9000" in rewritten


def test_rewrite_native_caddyfile_for_bridge_mode_falls_back_to_alias():
    ssh = DummySSH(responses=[(1, "", "")])
    mgr = ProxyManager(ssh)
    original = "example.com {\n    reverse_proxy localhost:9000\n}\n"
    rewritten = mgr.rewrite_native_caddyfile_for_bridge_mode(original)
    assert f"reverse_proxy {PROXY_HOST_GATEWAY_NAME}:9000" in rewritten


def test_native_config_uses_loopback_upstreams_true_localhost():
    mgr = ProxyManager(DummySSH())
    content = "example.com {\n  reverse_proxy localhost:9000\n}\n"
    assert mgr.native_config_uses_loopback_upstreams(content) is True


def test_native_config_uses_loopback_upstreams_true_127001():
    mgr = ProxyManager(DummySSH())
    content = "example.com {\n  reverse_proxy 127.0.0.1:19000\n}\n"
    assert mgr.native_config_uses_loopback_upstreams(content) is True


def test_native_config_uses_loopback_upstreams_false_non_loopback():
    mgr = ProxyManager(DummySSH())
    content = "example.com {\n  reverse_proxy 10.0.0.5:9000\n}\n"
    assert mgr.native_config_uses_loopback_upstreams(content) is False


def test_stop_native_caddy_success():
    # stop command + systemctl is-active -> inactive
    ssh = DummySSH(responses=[(0, "", ""), (0, "inactive\n", "")])
    ok = ProxyManager(ssh).stop_native_caddy()
    assert ok is True


def test_stop_native_caddy_failure_if_process_still_running():
    ssh = DummySSH(responses=[(0, "", ""), (0, "active\n", "")])
    ok = ProxyManager(ssh).stop_native_caddy()
    assert ok is False
