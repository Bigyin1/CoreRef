"""Test SDK workspace changes and the Nix arguments used by project commands."""

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from coreref import Dependency, Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    (tmp_path / "flake.nix").write_text("{}")
    lock = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"flow": "flow", "shared": ["flow", "common"]}},
            "flow": {"inputs": {"common": "common"}, "locked": {"rev": "flow-revision"}},
            "common": {"flake": False, "locked": {"rev": "common-revision"}},
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock))
    return Project(tmp_path)


def test_dependencies_are_read_from_lock(project: Project) -> None:
    assert project.dependencies() == (
        Dependency("flow", "flow-revision", None),
        Dependency("shared", "common-revision", None),
    )


def test_nested_workspace_is_persistent_and_keeps_lock(project: Project, tmp_path: Path) -> None:
    source = tmp_path / "new common"
    source.mkdir()
    lock = (tmp_path / "flake.lock").read_bytes()
    assert project.add_editable("flow/common", source) == source.resolve()
    assert project.add_editable("flow/common", source) == source.resolve()
    assert Project(tmp_path).editables() == {"flow/common": source.resolve()}

    with (
        patch("coreref.project.shutil.which", return_value="/usr/bin/nix"),
        patch("coreref.project.subprocess.run") as run,
    ):
        run.return_value = CompletedProcess([], 0, stdout="/nix/store/example\n")
        assert project.build("rtl", options=("--no-link",)) == (Path("/nix/store/example"),)
        command = run.call_args.args[0]
        assert command[1:4] == ["build", f"{tmp_path.resolve()}#rtl", "--print-out-paths"]
        assert command[4:] == [
            "--override-input",
            "flow/common",
            f"path:{source.resolve()}".replace(" ", "%20"),
            "--no-link",
        ]

    assert project.remove_editable("flow/common")
    assert not project.remove_editable("flow/common")
    assert project.editables() == {}
    assert (tmp_path / "flake.lock").read_bytes() == lock


def test_workspace_rejects_missing_input_and_non_flake(project: Project, tmp_path: Path) -> None:
    source = tmp_path / "plain-files"
    source.mkdir()
    with pytest.raises(ValueError, match="Unknown flake input"):
        project.add_editable("other", source)
    with pytest.raises(ValueError, match=r"Expected flake\.nix"):
        project.add_editable("flow", source)
    assert project.editables() == {}
