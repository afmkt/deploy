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





def test_service_init_requires_image_in_non_interactive_mode():
    runner = CliRunner()
    result = runner.invoke(cli, ["--non-interactive", "svc", "init"])

    assert result.exit_code == 2
    assert "--image is required" in result.output



def test_service_init_rejects_internal_flag():
    """--internal must no longer exist as a CLI option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["svc", "init", "--internal"])
    assert result.exit_code == 2
    assert "No such option" in result.output



