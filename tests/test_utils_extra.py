import pytest
from deploy.utils import get_ssh_key_path
from pathlib import Path
import os

def test_get_ssh_key_path_none(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert get_ssh_key_path() is None

def test_get_ssh_key_path_found(monkeypatch):
    keys = {"id_ed25519": True, "id_rsa": False, "id_ecdsa": False}
    def fake_exists(self):
        return keys.get(self.name, False)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/fakehome")))
    assert get_ssh_key_path() == "/fakehome/.ssh/id_ed25519"
