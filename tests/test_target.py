"""Tests for shared deployment target helpers."""

from deploy.local import LocalConnection
from deploy.ssh import SSHConnection
from deploy.target import (
    construct_repo_url,
    display_target,
    docker_push_args_for_connection,
    import_source_label,
    is_local_host,
    needs_remote_identity,
    push_args_for_connection,
    proxy_healthcheck_url,
    resolve_target,
)


def test_is_local_host_aliases():
    assert is_local_host("local") is True
    assert is_local_host("localhost") is True
    assert is_local_host("127.0.0.1") is True
    assert is_local_host("::1") is True
    assert is_local_host("example.com") is False


def test_resolve_target_prefers_localhost():
    assert resolve_target("local") == "local"
    assert resolve_target("localhost") == "local"
    assert resolve_target("example.com") == "remote"
    assert resolve_target(None) == "local"
    assert resolve_target("") == "local"


def test_display_target_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", username="root")
    assert display_target(local) == "local machine"
    assert display_target(remote) == "root@example.com:22"


def test_import_source_label_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", username="root")
    assert import_source_label(local) == "local"
    assert import_source_label(remote) == "example.com"


def test_proxy_healthcheck_url_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", username="root")
    assert proxy_healthcheck_url(local) == "http://localhost/healthz"
    assert proxy_healthcheck_url(remote) == "http://example.com/healthz"


def test_docker_push_args_for_connection_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", port=2222, username="root", key_filename="/tmp/key")
    assert docker_push_args_for_connection("nginx:latest", local) == [
        "--image",
        "nginx:latest",
        "--no-interactive",
        "--host",
        "localhost",
    ]
    assert docker_push_args_for_connection("nginx:latest", remote) == [
        "--image",
        "nginx:latest",
        "--no-interactive",
        "--host",
        "example.com",
        "--port",
        "2222",
        "--username",
        "root",
        "--key",
        "/tmp/key",
    ]


def test_push_args_for_connection_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", port=2222, username="root", key_filename="/tmp/key")
    assert push_args_for_connection(".", "/tmp/deploy/repos", local) == [
        "--repo-path",
        ".",
        "--deploy-path",
        "/tmp/deploy/repos",
        "--no-interactive",
        "--host",
        "localhost",
    ]
    assert push_args_for_connection(".", "/tmp/deploy/repos", remote) == [
        "--repo-path",
        ".",
        "--deploy-path",
        "/tmp/deploy/repos",
        "--no-interactive",
        "--host",
        "example.com",
        "--port",
        "2222",
        "--username",
        "root",
        "--key",
        "/tmp/key",
    ]


def test_needs_remote_identity_only_for_incomplete_remote():
    local = LocalConnection(username="tester")
    remote_missing = SSHConnection(host="", username="")
    remote_ok = SSHConnection(host="example.com", username="root")
    assert needs_remote_identity(local) is False
    assert needs_remote_identity(remote_missing) is True
    assert needs_remote_identity(remote_ok) is False


def test_construct_repo_url_local_and_remote():
    local = LocalConnection(username="tester")
    remote = SSHConnection(host="example.com", port=2222, username="root")
    assert construct_repo_url("/tmp/deploy/repos/myrepo.git", local) == "/tmp/deploy/repos/myrepo.git"
    assert construct_repo_url("/tmp/deploy/repos/myrepo.git", remote) == "ssh://root@example.com:2222/tmp/deploy/repos/myrepo.git"