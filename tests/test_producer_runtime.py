"""Validate the producer's portable Python environment before recipe publication."""

from pathlib import Path

import pytest

from coreref._internal.runtime import ProducerRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> ProducerRuntime:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "build-fixture"\n')
    (tmp_path / ".python-version").write_text("cpython@3.13.14\n")
    (tmp_path / "uv.lock").write_text(
        """version = 1

[[package]]
name = "build-fixture"
source = {virtual = "."}

[[package]]
name = "build-tool"
source = {registry = "https://example.invalid/simple"}
"""
    )
    return ProducerRuntime(tmp_path)


def test_runtime_accepts_an_exact_python_pin_with_registry_dependencies(
    runtime: ProducerRuntime,
) -> None:
    runtime.validate()


@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock", ".python-version"])
def test_runtime_requires_each_environment_file(runtime: ProducerRuntime, filename: str) -> None:
    (runtime.project / filename).unlink()

    with pytest.raises(ValueError, match="Publishing rebuild recipes requires") as error:
        runtime.validate()

    assert filename in str(error.value)


@pytest.mark.parametrize("version", ["3.13", "pypy@3.13.14", "3.13.14rc1"])
def test_runtime_rejects_inexact_or_unsupported_python_pins(
    runtime: ProducerRuntime, version: str
) -> None:
    (runtime.project / ".python-version").write_text(version)

    with pytest.raises(ValueError, match="must pin an exact CPython version"):
        runtime.validate()


@pytest.mark.parametrize(
    "source",
    [
        '{editable = "../build-tool"}',
        '{git = "https://example.invalid/build-tool"}',
        '{registry = "file:///cache/simple"}',
    ],
)
def test_runtime_rejects_unpublished_dependency_sources(
    runtime: ProducerRuntime, source: str
) -> None:
    lockfile = runtime.project / "uv.lock"
    lockfile.write_text(
        lockfile.read_text().replace('{registry = "https://example.invalid/simple"}', source)
    )

    with pytest.raises(ValueError, match="build-tool from a Python registry"):
        runtime.validate()
