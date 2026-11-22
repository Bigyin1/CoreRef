"""The standard CoreRef manifest."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from ..._internal.environment import ImagePin, ToolPins, checked_command
from ..errors import InvalidPackage
from ..packages.records import JSON, Dependency, LocalRecord, PackageInput, Ref, _parse_dependency

_OptionValue = str | int | bool


class _ConfiguredDependency(BaseModel):
    """A named configuration of a package recipe."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    ref: str
    options: dict[str, _OptionValue] = Field(min_length=1)


class Manifest(BaseModel):
    """Build configuration shared by CoreRef projects."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ref: str
    type: str | None = Field(default=None, min_length=1)
    flow_version: str | None = Field(default=None, min_length=1)
    release_through: str | None = Field(default=None, min_length=1)
    dependencies: list[str | _ConfiguredDependency] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    tools: ToolPins = Field(default_factory=dict)
    options: dict[str, list[_OptionValue]] = Field(default_factory=dict)
    payload: JsonValue = None

    @classmethod
    def load(cls, path: Path, *, flow_package: str | None = None) -> Manifest:
        """Read a manifest from YAML and optionally verify the active flow package."""
        try:
            manifest = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (yaml.YAMLError, ValidationError) as error:
            raise InvalidPackage(f"Invalid manifest {path}: {error}") from error
        if flow_package is not None:
            manifest._check_flow_version(path, flow_package)
        return manifest

    def _check_flow_version(self, path: Path, package: str) -> None:
        if self.flow_version is None:
            raise ValueError(f"{path} must declare flow_version for {package}")
        requirement = f"{package}=={self.flow_version}"
        try:
            installed = version(package)
        except PackageNotFoundError as error:
            raise ValueError(
                f"{path} requires {requirement}. Install it in the project venv."
            ) from error
        if installed != self.flow_version:
            raise ValueError(
                f"{path} requires {requirement}. The active environment has {package}=={installed}."
            )

    def image(self, name: str) -> str | None:
        """Read the pinned image for this tool, if configured to run in a container."""
        pin = self.tools.get(name)
        return pin.image if isinstance(pin, ImagePin) else None

    def command(self, name: str) -> tuple[str, str]:
        """Resolve the local tool and return its executable path and verified version."""
        return checked_command(self.tools, name)

    def local_record(self, source: Path) -> LocalRecord:
        """Use the project directory as SCM source and as the base for file paths."""
        ref = Ref(self.ref)
        if ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        return LocalRecord(
            ref,
            cast(JSON, self.payload),
            source=source,
            files={
                name: (source / Path(path).expanduser()).resolve()
                for name, path in self.files.items()
            },
        )

    @property
    def requirements(self) -> tuple[Dependency, ...]:
        return tuple(_parse_dependency(item) for item in self.dependencies if isinstance(item, str))

    @property
    def configured_dependencies(self) -> dict[str, PackageInput]:
        return {
            item.name: PackageInput(Ref(item.ref), item.options)
            for item in self.dependencies
            if isinstance(item, _ConfiguredDependency)
        }

    @model_validator(mode="after")
    def _unique_dependencies(self) -> Manifest:
        configured_names = [
            item.name for item in self.dependencies if isinstance(item, _ConfiguredDependency)
        ]
        if len(set(configured_names)) != len(configured_names):
            raise ValueError("Duplicate configured dependency name")
        requirements = self.requirements
        if len({ref.package_name for ref in requirements}) != len(requirements):
            raise ValueError("Duplicate package in plain dependencies")
        return self
