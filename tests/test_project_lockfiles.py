"""Project-owned Conan lockfiles stay opaque and outside published packages."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from hwcatalog._internal.backends.conan_format import _recipe_text, write_recipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import BackendUnavailable, InvalidPackage, InvalidPublication
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend
from hwcatalog.backends.memory import MemoryBackend


def test_project_lock_pins_publication_and_installation(tmp_path: Path) -> None:
    backend = ConanBackend(tmp_path / "cache")
    dependency = Record(Ref("cpu", "1.0", "rtl", "soc"))
    result = Ref("cpu", "4.0", "lib-lef", "soc")
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    description = tmp_path / "package.json"
    description.write_text(
        json.dumps(
            {
                "ref": asdict(result),
                "dependencies": [asdict(dependency.ref)],
                "files": {"cpu.lib": str(artifact)},
            }
        )
    )
    with Catalog(backend) as catalog:
        catalog.publish(dependency)
        lockfile = catalog.create_lockfile((dependency.ref,), tmp_path / "project/conan.lock")
        original_lock = lockfile.read_bytes()
        record = catalog.publish_file(description, lockfile=lockfile)
        assert record.files == ("cpu.lib",)
        _, recipe = backend._locate(result)
        assert not (recipe / "conan.lock").exists()
        assert catalog.graph.dependency_closure(result, lockfile=lockfile).dependencies == (
            Dependency(result, dependency.ref),
        )
        installed = catalog.materialize(result, lockfile=lockfile)
        assert (installed.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
        assert not (installed.path / "conan.lock").exists()
        assert lockfile.read_bytes() == original_lock
        assert catalog.create_lockfile((result,), lockfile) == lockfile
        assert catalog.graph.dependency_closure(result, lockfile=lockfile).records


@pytest.mark.parametrize(
    "damage,exception",
    [
        ("missing", BackendUnavailable),
        ("incomplete", InvalidPackage),
        ("malformed", BackendUnavailable),
    ],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception]
) -> None:
    backend = ConanBackend(tmp_path / "cache")
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"))
    macro = Record(Ref("cpu", "4.0", "lib-lef", "soc"))
    with Catalog(backend) as catalog:
        catalog.publish(rtl)
        catalog.publish(macro, dependencies=(rtl.ref,))
        lockfile = catalog.create_lockfile((rtl.ref,), tmp_path / "project/conan.lock")
        if damage == "missing":
            lockfile.unlink()
        elif damage == "incomplete":
            backend._client.run(
                "lock",
                "remove",
                "--requires=soc.cpu.rtl/*",
                "--lockfile",
                str(lockfile),
                "--lockfile-out",
                str(lockfile),
            )
        else:
            lockfile.write_text("not a Conan lockfile\n")
        with pytest.raises(exception):
            catalog.graph.dependency_closure(macro.ref, lockfile=lockfile)
        assert catalog.get(macro.ref) == macro


def test_empty_project_and_memory_lockfile_contract(tmp_path: Path) -> None:
    with Catalog(ConanBackend(tmp_path / "cache")) as catalog:
        lockfile = catalog.create_lockfile((), tmp_path / "empty/conan.lock")
        empty_lock = lockfile.read_bytes()
        root = Record(Ref("root", "1.0", "rtl", "soc"))
        catalog.publish(root, lockfile=lockfile)
        assert catalog.graph.dependency_closure(root.ref, lockfile=lockfile).records == (root,)
        catalog.create_lockfile((root.ref,), lockfile)
        catalog.create_lockfile((), lockfile)
        assert lockfile.read_bytes() == empty_lock
    with Catalog(MemoryBackend()) as memory:
        operations = (
            lambda: memory.create_lockfile((), tmp_path / "unsupported.lock"),
            lambda: memory.publish(root, lockfile=lockfile),
            lambda: memory.publish_file(tmp_path / "unused.json", lockfile=lockfile),
            lambda: memory.materialize(root.ref, lockfile=lockfile),
            lambda: memory.graph.dependency_closure(root.ref, lockfile=lockfile),
        )
        for operation in operations:
            with pytest.raises(InvalidPublication, match="require a Conan backend"):
                operation()
        assert not (tmp_path / "unsupported.lock").exists()


@pytest.mark.parametrize("format_version", [1, 2, 3])
def test_legacy_recipe_with_embedded_lockfile_remains_readable(
    tmp_path: Path, format_version: int
) -> None:
    backend = ConanBackend(tmp_path / "cache")
    record = Record(Ref("cpu", "1.0", "rtl", "soc"))
    recipe = tmp_path / "legacy"
    write_recipe(recipe, record)
    (recipe / "hwcatalog.json").write_text(json.dumps({"format": format_version}))
    (recipe / "conanfile.py").write_text(
        _recipe_text(record.ref, (), format_version=format_version)
    )
    backend._client.create_lockfile(recipe)
    backend._client.export(recipe)
    with Catalog(backend) as catalog:
        assert catalog.get(record.ref) == record
        assert catalog.graph.dependency_closure(record.ref).records == (record,)
