"""Check generated routes without loading YAML at execution time."""

from pathlib import Path
from runpy import run_path

import pytest

from coreref.api.build.codegen import generate_route
from coreref.api.packages import Catalog
from coreref.api.projects import Manifest, Project


@pytest.mark.nix
def test_generated_route_composes_transforms_and_binds_file_roles(
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
      configuration: {prefix: "name: "}
  extra:
    - name: upper
      transform: demo_transforms:upper
      needs: [read]
      outputs: [result.txt]
""",
        encoding="utf-8",
    )
    generated = tmp_path / "generated_route.py"
    module = generate_route(definition)
    assert generate_route(definition) == module
    generated.write_text(module, encoding="utf-8")
    definition.unlink()

    project = Project(tmp_path, Manifest(ref="demo.codegen/1.0"), Catalog(tmp_path / "home"))
    loaded = run_path(str(generated))
    project.route = loaded["make_route"](("base", "extra"))
    project.stages = loaded["bind_stages"](project, project.route)
    with pytest.raises(ValueError, match="project-input"):
        project.build("upper")

    source = tmp_path / "source-with-custom-name.txt"
    source.write_text("   core ref \n", encoding="utf-8")
    project.stages = loaded["bind_stages"](project, project.route, files={"project-input": source})
    assert project.route.order == ("read", "upper")
    assert project.build("upper")["result.txt"].read_text(encoding="utf-8") == "NAME: CORE REF"


def test_generation_rejects_invalid_python_symbols(tmp_path: Path) -> None:
    definition = tmp_path / "stage-map.yml"
    definition.write_text(
        "modules: {rtl: [{name: rtl, outputs: [], transform: 'flow:invalid()'}]}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Expected module:symbol"):
        generate_route(definition)
