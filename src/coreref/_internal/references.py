"""Pure conversions between catalog coordinates and Conan references."""

from __future__ import annotations

import re

from ..api.errors import InvalidPackage
from ..api.packages.records import Ref

NAME_PART = re.compile(r"[a-z0-9_-]+")
VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{1,100}")
REVISION = re.compile(r"[a-z0-9]+")
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200


def parse_package_name(name: str) -> tuple[str, str]:
    namespace, _, component = name.partition(".")
    if namespace.startswith("-") or not (
        NAME_PART.fullmatch(namespace) and NAME_PART.fullmatch(component)
    ):
        raise InvalidPackage("Expected lowercase namespace.component")
    if len(name) > _MAX_NAME_LENGTH:
        raise InvalidPackage("Reference exceeds Conan name constraints")
    return namespace, component


def validate_version_range(version: str) -> None:
    expression = version[1:-1]
    if (
        not (version.startswith("[") and version.endswith("]"))
        or not expression.strip()
        or any(character in expression for character in "#/@[]:%\r\n")
    ):
        raise InvalidPackage("Expected a Conan version range in brackets without a revision")
    from conan.errors import ConanException  # type: ignore[import-untyped]
    from conan.internal.model.version_range import VersionRange  # type: ignore[import-untyped]

    try:
        VersionRange(expression)
    except ConanException as error:
        raise InvalidPackage(f"Invalid Conan version range: {version}") from error


def to_conan_reference(ref: Ref) -> str:
    """Encode an exact canonical reference."""
    if not isinstance(ref, Ref):
        raise InvalidPackage("This operation requires a Ref with an exact version")
    if len(f"{ref.package_name}/{ref.version}") > _MAX_REFERENCE_LENGTH or not VERSION.fullmatch(
        ref.version
    ):
        raise InvalidPackage("Reference exceeds Conan name/version constraints")
    if ref.revision is not None and (
        not REVISION.fullmatch(ref.revision) or ref.revision == "latest"
    ):
        raise InvalidPackage(f"Invalid concrete recipe revision: {ref.revision!r}")
    return str(ref)


def from_conan_reference(value: str) -> Ref:
    """Decode coordinates and a recipe revision, omitting transport timestamps."""
    return Ref(value.split("%", 1)[0])
