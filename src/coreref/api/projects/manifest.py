"""Parse the standard manifest for CoreRef projects.

The YAML captures package coordinates, dependencies, options, tool
pins and package inputs. A flow interprets the project type and defines
the build route separately.
"""

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
    """Validate project declarations shared by CoreRef and a flow.

    Only ``ref`` is required. A dependency can be a reference, a
    version range, or a named configuration with options. The ``tools``
    field accepts named digests and command pins or a list of ``name/version``
    references and named mappings. ``files``
    maps logical artifact names to filesystem paths rooted at the project
    when relative. ``payload`` is optional flow metadata.
    """

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
        """Read and validate a YAML manifest at ``path``.

        If ``flow_package`` is supplied, require a ``flow_version`` and
        check it against the installed Python distribution. Invalid YAML
        or schema data raises ``InvalidPackage``.
        """
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
                f"{path} requires {requirement}. Run the project's pinned flow environment."
            ) from error
        if installed != self.flow_version:
            raise ValueError(
                f"{path} requires {requirement}. The active environment has {package}=={installed}."
            )

    def image(self, name: str) -> str | None:
        """Return the digest-pinned OCI image for a tool, if configured.

        An absent tool or a local command pin yields ``None``.
        """
        pin = self.tools.get(name)
        return pin.image if isinstance(pin, ImagePin) else None

    def command(
        self, name: str, *, version_args: tuple[str, ...] = ("--version",)
    ) -> tuple[str, str]:
        """Find a pinned local command and verify its reported version.

        Resolve the configured command, or the tool name by default, on
        ``PATH`` and run it with the flow's ``version_args``. Return the executable path
        and the verified version. A missing executable raises
        ``FileNotFoundError``. A missing pin or mismatched version raises
        ``ValueError``.
        """
        return checked_command(self.tools, name, version_args=version_args)

    def local_record(self, source: Path) -> LocalRecord:
        """Create a working publication record for the supplied checkout.

        Use ``source`` as the SCM checkout and resolve relative entries in
        ``files`` against it. The project's root reference must omit a recipe
        revision. Dependency requirements are available separately.
        """
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
        """Return the ordinary references and version ranges in manifest order."""
        return tuple(_parse_dependency(item) for item in self.dependencies if isinstance(item, str))

    @property
    def configured_dependencies(self) -> dict[str, PackageInput]:
        """Return option-dependent inputs keyed by their manifest names.

        Different names can select distinct option sets for the same recipe.
        """
        return {
            item.name: PackageInput(_parse_dependency(item.ref), item.options)
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
