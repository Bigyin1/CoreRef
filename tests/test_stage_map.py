"""Check selection, binding and execution from a Flow stage map."""

from pathlib import Path

import pytest

from coreref.api.build import Route, Stage, StageMap, StageSpec
from coreref.api.errors import InvalidPackage
from coreref.api.packages import Catalog
from coreref.api.projects import Manifest, Project

from . import stages as transforms


@pytest.mark.nix
def test_stage_map_composes_transforms_and_binds_file_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "demo_transforms.py").write_text(
        """
def read(work, log, config):
    text = (work / "raw.txt").read_text()
    (work / "clean.txt").write_text(config["prefix"] + text.strip())

def upper(work, log, config):
    (work / "result.txt").write_text((work / "clean.txt").read_text().upper())
""",
        encoding="utf-8",
    )
    definition = tmp_path / "stage-map.yml"
    definition.write_text(
        """
modules:
  base:
    - name: read
      transform: demo_transforms:read
      outputs: [clean.txt]
      files: {raw.txt: project-input}
      configuration: {prefix: 'Имя "${RTL_ROOT}": '}
  extra:
    - name: upper
      transform: demo_transforms:upper
      needs: [read]
      outputs: [result.txt]
""",
        encoding="utf-8",
    )
    project = Project(tmp_path, Manifest(ref="demo.stages/1.0"), Catalog(tmp_path / "home"))
    stages = StageMap.load(definition)
    project.route = stages.route(("base", "extra"))
    project.stages = stages.bind(project, project.route)
    with pytest.raises(ValueError, match="project-input"):
        project.build("upper")

    source = tmp_path / "source-with-custom-name.txt"
    source.write_text("   core ref \n", encoding="utf-8")
    project.stages = stages.bind(project, project.route, files={"project-input": source})
    assert project.route.order == ("read", "upper")
    assert project.build("upper")["result.txt"].read_text(encoding="utf-8") == (
        'ИМЯ "${RTL_ROOT}": CORE REF'
    )


def test_stage_map_rejects_invalid_python_symbols(tmp_path: Path) -> None:
    definition = tmp_path / "stage-map.yml"
    definition.write_text(
        "modules: {rtl: [{name: rtl, outputs: [], transform: 'flow:invalid()'}]}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Expected module:symbol"):
        StageMap.load(definition)


@pytest.mark.nix
def test_a_file_requires_a_declared_predecessor(tmp_path: Path) -> None:
    project = Project(tmp_path, Manifest(ref="demo.inputs/1.0"), Catalog())
    project.route = Route(
        [
            StageSpec(name="prepare", outputs=("small.txt", "other.txt")),
            StageSpec(name="upper", outputs=("upper.txt",)),
        ]
    )
    project.stages = {
        "prepare": Stage(transform=transforms.prepare, files={"input.txt": tmp_path / "input.txt"}),
        "upper": Stage(transform=transforms.upper, inputs=("small.txt",)),
    }
    with pytest.raises(InvalidPackage, match=r"undeclared inputs: small\.txt"):
        project.build(("prepare", "upper"))
