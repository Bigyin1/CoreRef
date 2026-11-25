"""Exercise the Flow command line without running Nix or Git."""

from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from coreref_flow.cli import app, main


@pytest.mark.parametrize(
    ("arguments", "project", "options"),
    [
        (["rtl", "--no-link"], ".", ["--no-link"]),
        (
            ["build", "rtl", "path:/project", "--option", "max-jobs", "2"],
            "path:/project",
            ["--option", "max-jobs", "2"],
        ),
    ],
)
def test_build_passes_options_to_nix(
    arguments: list[str], project: str, options: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    output = Path("/nix/store/test-rtl")
    with patch("coreref_flow.cli.Project") as sdk:
        sdk.return_value.build.return_value = (output,)
        with pytest.raises(SystemExit) as exit_status:
            main(arguments)
        sdk.assert_called_once_with(project)
        sdk.return_value.build.assert_called_once_with("rtl", options=options)
    assert exit_status.value.code == 0
    assert capsys.readouterr().out == f"{output}\n"


def test_workspace_checkout_accepts_a_destination(tmp_path: Path) -> None:
    checkout = tmp_path / "local-rtl"
    with patch("coreref_flow.cli.Project") as sdk:
        sdk.return_value.checkout.return_value = checkout
        result = CliRunner().invoke(
            app, ["workspace", "checkout", "core", str(checkout), "--project", str(tmp_path)]
        )
        sdk.assert_called_once_with(str(tmp_path))
        sdk.return_value.checkout.assert_called_once_with("core", checkout)
    assert result.exit_code == 0
    assert result.stdout == f"{checkout}\n"


def test_cli_preserves_nix_exit_code() -> None:
    with patch("coreref_flow.cli.Project") as sdk:
        sdk.return_value.build.side_effect = CalledProcessError(42, "nix")
        with pytest.raises(SystemExit) as exit_status:
            main(["build", "rtl"])
    assert exit_status.value.code == 42
