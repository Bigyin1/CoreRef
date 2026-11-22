"""Verify recipe-level control over on-demand builds in a Conan graph."""

from pathlib import Path

import pytest

from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.packages import Catalog, PackageInput, Ref
from coreref.testing import LocalServer


def test_missing_variant_builds_only_allowed_exact_prerequisites(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    writer = Catalog(conan_server.configure(tmp_path / "writer"))
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
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
    prerequisite = writer._variants.publish_recipe(source)
    recipe.write_text(template.format(name="stages.output", requires=(str(prerequisite),)))
    target = PackageInput(writer._variants.publish_recipe(source), {})
    wrong = Ref(f"{prerequisite.without_revision()}#other")

    with pytest.raises(NotFound, match="package binary"):
        reader._variants._get(
            target, lockfile=None, build_missing=True, build_prerequisites=(wrong,)
        )
    with pytest.raises(InvalidPackage, match="exact recipe revisions"):
        reader._variants._get(
            target,
            lockfile=None,
            build_missing=True,
            build_prerequisites=(prerequisite.without_revision(),),
        )

    installed = reader._variants._get(
        target, lockfile=None, build_missing=True, build_prerequisites=(prerequisite,)
    )
    assert (installed.directory / "result").read_text() == "output"
    assert reader.get_variant(target, lockfile=None).binary_reference == installed.binary_reference


def test_generated_recipe_builds_configurations_from_exported_inputs(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    writer = Catalog(conan_server.configure(tmp_path / "writer"))
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    input_file = tmp_path / "input.txt"
    input_file.write_text("result", encoding="utf-8")
    runner = tmp_path / "generate.py"
    runner.write_text(
        """def generate(options, source, output):
    value = (source / "config/input.txt").read_text(encoding="utf-8")
    (output / "result.txt").write_text(
        f'{value}:{options["size"]}:{options["label"]}:{options["enabled"]}', encoding="utf-8"
    )
""",
        encoding="utf-8",
    )

    ref = writer.publish_variant_recipe(
        Ref("tools.generated/1.0"),
        options={"size": (8, 4), "label": ("fast", "slow"), "enabled": (True, False)},
        inputs={"config/input.txt": input_file},
        runner=runner,
    )
    runner.unlink()
    input_file.unlink()

    default = reader.get_variant(PackageInput(ref, {}), lockfile=None, build_missing=True)
    custom = reader.get_variant(
        PackageInput(ref, {"size": 4, "label": "slow", "enabled": False}),
        lockfile=None,
        build_missing=True,
    )
    assert default.ref == custom.ref == ref
    assert ref.revision is not None
    assert default.package_id != custom.package_id
    assert (default.directory / "result.txt").read_text() == "result:8:fast:True"
    assert (custom.directory / "result.txt").read_text() == "result:4:slow:False"


@pytest.mark.parametrize("name", [".", "/absolute", "../outside", "directory//input"])
def test_generated_recipe_rejects_unsafe_names(name: str, tmp_path: Path) -> None:
    variants = Catalog(tmp_path / "home")
    runner = Path(__file__)
    with pytest.raises(InvalidPackage, match="Invalid recipe input path"):
        variants.publish_variant_recipe(
            Ref("tools.generated/1.0"), options={}, inputs={name: runner}, runner=runner
        )


@pytest.mark.parametrize(
    ("reference", "values", "message"),
    [
        ("tools.generated/1.0#old", (4,), "without revision"),
        ("tools.generated/1.0", (), "at least one allowed value"),
    ],
)
def test_generated_recipe_rejects_invalid_configuration(
    reference: str, values: tuple[int, ...], message: str, tmp_path: Path
) -> None:
    variants = Catalog(tmp_path / "home")
    with pytest.raises(InvalidPackage, match=message):
        variants.publish_variant_recipe(
            Ref(reference), options={"size": values}, inputs={}, runner=Path(__file__)
        )
