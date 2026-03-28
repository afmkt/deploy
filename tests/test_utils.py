import pytest
from deploy.utils import validate_host, validate_port, validate_path


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
