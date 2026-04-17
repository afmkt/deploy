from types import SimpleNamespace

from deploy.target import image_push_args_for_connection, push_args_for_connection


def test_image_push_args_for_remote_connection():
    connection = SimpleNamespace(
        is_local=False,
        host="example.com",
        port=2222,
        username="alice",
        key_filename="/tmp/key",
    )

    assert image_push_args_for_connection("repo/app:latest", connection) == [
        "--image",
        "repo/app:latest",
        "--non-interactive",
        "--remote",
        "example.com",
        "--port",
        "2222",
        "--username",
        "alice",
        "--key",
        "/tmp/key",
    ]


def test_push_args_for_local_connection():
    connection = SimpleNamespace(
        is_local=True,
        host="localhost",
        port=22,
        username="",
        key_filename=None,
    )

    assert push_args_for_connection(".", "/tmp/deploy/repos", connection) == [
        "--repo-path",
        ".",
        "--path",
        "/tmp/deploy/repos",
        "--non-interactive",
        "--remote",
        "localhost",
    ]
