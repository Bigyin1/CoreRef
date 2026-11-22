"""Verify recipe-level control over on-demand builds in a Conan graph."""

from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.records import Ref
from coreref.testing import LocalServer
from coreref.tools.variants import PackageInput, Variants


def test_missing_variant_builds_only_allowed_exact_prerequisites(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    writer = Variants(Catalog(conan_server.configure(tmp_path / "writer")))
    reader = Variants(Catalog(conan_server.configure(tmp_path / "reader")))
    source = tmp_path / "producer"
    source.mkdir()
    recipe = source / "conanfile.py"
    template = """from conan import ConanFile
from conan.tools.files import save


class Producer(ConanFile):
    name = {name!r}
    version = "1.0"
    requires = {requires!r}

    def package(self):
        save(self, self.package_folder + "/result", "output")
"""
    recipe.write_text(template.format(name="stages.input", requires=()))
    prerequisite = writer.publish_recipe(source)
    recipe.write_text(template.format(name="stages.output", requires=(str(prerequisite),)))
    target = PackageInput(writer.publish_recipe(source), {})
    wrong = Ref(f"{prerequisite.without_revision()}#other")

    with pytest.raises(NotFound, match="package binary"):
        reader._get(target, lockfile=None, build_missing=True, build_prerequisites=(wrong,))
    with pytest.raises(InvalidPackage, match="exact recipe revisions"):
        reader._get(
            target,
            lockfile=None,
            build_missing=True,
            build_prerequisites=(prerequisite.without_revision(),),
        )

    installed = reader._get(
        target, lockfile=None, build_missing=True, build_prerequisites=(prerequisite,)
    )
    assert (installed.directory / "result").read_text() == "output"
    assert reader.get(target, lockfile=None).binary_reference == installed.binary_reference
