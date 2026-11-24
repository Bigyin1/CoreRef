"""Check the short and expanded tool declarations in a project manifest."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from coreref.api.packages import Ref
from coreref.api.projects import Manifest


def test_tools_list_with_host_commands_and_image(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    image = "openroad/orfs@sha256:" + "a" * 64
    path.write_text(
        f"""ref: demo.soc/1.0
dependencies: [demo.cpu/1.0]
tools:
  - genus/23.14-s087
  - innovus/24.1
  - openroad:
      image: {image}
  - simulator:
      version: "24.03"
      command: xrun
""",
        encoding="utf-8",
    )
    manifest = Manifest.load(path)
    assert manifest.requirements == (Ref("demo.cpu/1.0"),)
    assert manifest.model_dump(exclude_none=True)["tools"] == {
        "genus": {"version": "23.14-s087"},
        "innovus": {"version": "24.1"},
        "openroad": {"image": image},
        "simulator": {"version": "24.03", "command": "xrun"},
    }
    assert manifest.image("openroad") == image
    assert Manifest.model_validate(manifest.model_dump()) == manifest


@pytest.mark.parametrize(
    ("tools", "message"),
    [
        (["genus"], "Expected tool name/version"),
        (["genus/"], "Expected tool name/version"),
        (["/24.1"], "Expected tool name/version"),
        (["genus/24.1/extra"], "Expected tool name/version"),
        (["genus/24.1 extra"], "Expected tool name/version"),
        ([24], "Expected tool name/version"),
        (["genus/24.1", "genus/24.2"], "Duplicate tool 'genus'"),
        (["genus/24.1", {"genus": {"version": "24.2"}}], "Duplicate tool 'genus'"),
    ],
)
def test_malformed_and_duplicate_tools(tools: list[object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Manifest.model_validate({"ref": "demo.soc/1.0", "tools": tools})
