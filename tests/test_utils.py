import pytest

from deploy.utils import prompt_connection_details, validate_host, validate_path, validate_port


def test_validate_host_valid():
    assert validate_host("example.com")
    assert validate_host("192.168.1.1")


def test_validate_host_invalid():
    assert not validate_host("")
    assert not validate_host("bad host")
    assert not validate_host("host\nname")


def test_validate_port_valid():
    assert validate_port(22)
    assert validate_port(65535)
    assert validate_port(1)


def test_validate_port_invalid():
    assert not validate_port(0)
    assert not validate_port(70000)


def test_validate_path_valid():
    assert validate_path("/tmp")
    assert validate_path(".")


def test_validate_path_invalid():
    assert not validate_path("")


def test_prompt_connection_details_skips_host_prompt_when_default_host_is_provided(monkeypatch):
    prompt_calls = []
    confirm_calls = []

    def fake_prompt(label, default="", **kwargs):
        prompt_calls.append((label, default))
        if label == "Port":
            return "22"
        if label == "Username":
            return "root"
        if label == "Password":
            return "secret"
        raise AssertionError(f"Unexpected prompt: {label}")

    def fake_confirm(label, default=False, **kwargs):
        confirm_calls.append((label, default))
        return False

    monkeypatch.setattr("deploy.utils.Prompt.ask", fake_prompt)
    monkeypatch.setattr("deploy.utils.Confirm.ask", fake_confirm)

    details = prompt_connection_details(default_host="47.100.30.18")

    assert details["host"] == "47.100.30.18"
    assert prompt_calls == [("Port", "22"), ("Username", ""), ("Password", "")]
    assert confirm_calls == [("Use SSH key authentication?", True)]


def test_prompt_connection_details_prompts_for_host_when_missing(monkeypatch):
    prompt_calls = []

    def fake_prompt(label, default="", **kwargs):
        prompt_calls.append((label, default))
        responses = {
            "Remote host (hostname or IP)": "47.100.30.18",
            "Port": "22",
            "Username": "root",
            "Password": "secret",
        }
        return responses[label]

    monkeypatch.setattr("deploy.utils.Prompt.ask", fake_prompt)
    monkeypatch.setattr("deploy.utils.Confirm.ask", lambda *args, **kwargs: False)

    details = prompt_connection_details()

    assert details["host"] == "47.100.30.18"
    assert prompt_calls[0] == ("Remote host (hostname or IP)", "")
