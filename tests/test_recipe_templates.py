"""Template rendering preserves existing canonical recipes and their revisions."""

from pathlib import Path

import pytest

from hwcatalog._internal.backends.conan_format import _recipe_text
from hwcatalog.api.models.records import Ref


@pytest.mark.parametrize("format_version", [1, 2, 3, 4])
def test_recipe_matches_the_pre_template_format(format_version: int) -> None:
    fixture = Path(__file__).parent / "fixtures" / "recipes" / f"format-{format_version}.txt"
    recipe = _recipe_text(Ref("cpu", "1.0", "rtl", "soc"), (), format_version=format_version)
    assert recipe.encode() == fixture.read_bytes()
