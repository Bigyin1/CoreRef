"""Parse the standard manifest for CoreRef projects.

The YAML captures package coordinates, dependencies, options, tool
pins and package inputs. A flow interprets the project type and defines
the build route separately.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from ..._internal.environment import CommandPin, EdaToolPins, ImagePin, checked_command
from ..._internal.references import requirement_name
from ..errors import InvalidPackage

_OptionValue = str | int | bool


class _ConfiguredDependency(BaseModel):
    """A named dependency with build options."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    ref: str
    options: dict[str, _OptionValue] = Field(min_length=1)


class Manifest(BaseModel):
    """Validate project declarations shared by CoreRef and a flow.

    Only ``ref`` is required. A dependency can be a reference, a
    version range, or a named configuration with options. ``eda_tools``
    accepts image digests, command pins and short ``name/version`` references
    for EDA tools. ``build_tools`` pins ordinary commands such as the C++
    compiler and Make. ``files`` maps logical artifacts to filesystem paths.
    ``payload`` is optional flow metadata.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    ref: str
    type: str | None = Field(default=None, min_length=1)
    flow_version: str | None = Field(default=None, min_length=1)
    release_through: str | None = Field(default=None, min_length=1)
    dependencies: list[str | _ConfiguredDependency] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    eda_tools: EdaToolPins = Field(default_factory=dict)
    build_tools: dict[str, CommandPin] = Field(default_factory=dict)
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
        """Return the digest-pinned OCI image for an EDA tool, if configured.

        An absent tool or a local command pin yields ``None``.
        """
        pin = self.eda_tools.get(name)
        return pin.image if isinstance(pin, ImagePin) else None

    def command(
        self, name: str, *, version_args: tuple[str, ...] = ("--version",)
    ) -> tuple[str, str]:
        """Find a pinned command in ``eda_tools`` or ``build_tools``.

        Resolve the configured command, or the tool name by default, on
        ``PATH`` and run it with the flow's ``version_args``. Return the executable path
        and the verified version. A missing executable raises
        ``FileNotFoundError``. A missing pin or mismatched version raises
        ``ValueError``.
        """
        pin = self.eda_tools.get(name) or self.build_tools.get(name)
        return checked_command(pin, name, version_args=version_args)

    @property
    def requirements(self) -> tuple[str, ...]:
        """Return plain dependency declarations in manifest order."""
        return tuple(item for item in self.dependencies if isinstance(item, str))

    @property
    def configured_dependencies(self) -> dict[str, _ConfiguredDependency]:
        """Return option-dependent inputs keyed by their manifest names.

        Different names can select distinct option sets for the same component.
        """
        return {
            item.name: item for item in self.dependencies if isinstance(item, _ConfiguredDependency)
        }

    @model_validator(mode="after")
    def _unique_dependencies(self) -> Manifest:
        configured = [item for item in self.dependencies if isinstance(item, _ConfiguredDependency)]
        if len({item.name for item in configured}) != len(configured):
            raise ValueError("Duplicate configured dependency name")
        for item in configured:
            requirement_name(item.ref)
        requirements = self.requirements
        if len({requirement_name(item) for item in requirements}) != len(requirements):
            raise ValueError("Duplicate package in plain dependencies")
        return self

    @model_validator(mode="after")
    def _unique_tools(self) -> Manifest:
        if shared := self.eda_tools.keys() & self.build_tools.keys():
            names = ", ".join(sorted(shared))
            raise ValueError(f"Tool names must be unique across eda_tools and build_tools: {names}")
        return self
