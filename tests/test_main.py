import pytest
from click.testing import CliRunner
from main import main

def test_main_help():
    runner = CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert "Git SSH Deploy Tool" in result.output
    assert "--repo-path" in result.output
