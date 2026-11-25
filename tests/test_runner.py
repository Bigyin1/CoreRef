"""Test version checks for site tools, before executing a licensed stage."""

from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from coreref._internal.nix_builder import ToolPin, verify_tool


@pytest.mark.parametrize(
    ("output", "valid"),
    [
        ("Example Tool version 1.2-hf3\n", True),
        ("Example Tool version 1.2-hf30\n", False),
        ("Example Tool version 1.2\n", False),
    ],
)
def test_version_is_a_complete_token(output: str, valid: bool) -> None:
    pin: ToolPin = {
        "executable": "example-tool",
        "module": "example/1.2-hf3",
        "version": "1.2-hf3",
        "versionArgs": ["--version"],
    }
    with patch("coreref._internal.nix_builder.subprocess.run") as run:
        run.return_value = CompletedProcess(["example-tool"], 0, stdout=output)
        if valid:
            verify_tool(pin)
        else:
            with pytest.raises(RuntimeError, match=r"required version 1\.2-hf3"):
                verify_tool(pin)
