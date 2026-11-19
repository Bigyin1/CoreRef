"""Pure conversions between catalog coordinates and Conan references."""

from __future__ import annotations

from conan.errors import ConanException  # type: ignore[import-untyped]
from conan.internal.model.version_range import VersionRange  # type: ignore[import-untyped]
from edify import RegexBuilder

from ..api.errors import InvalidPackage
from ..api.records import Ref

_ALPHANUMERIC = RegexBuilder().any_of().range("a", "z").range("0", "9").end()
_NAME_CHARACTER = RegexBuilder().any_of().subexpression(_ALPHANUMERIC).any_of_chars("_-").end()
_VERSION_START = RegexBuilder().any_of().subexpression(_ALPHANUMERIC).char("_").end()
_VERSION_CHARACTER = RegexBuilder().any_of().subexpression(_ALPHANUMERIC).any_of_chars("_+.-").end()

NAME_PART = RegexBuilder().one_or_more().subexpression(_NAME_CHARACTER).to_regex()
VERSION = (
    RegexBuilder()
    .subexpression(_VERSION_START)
    .between(1, 100)
    .subexpression(_VERSION_CHARACTER)
    .to_regex()
)
REVISION = RegexBuilder().one_or_more().subexpression(_ALPHANUMERIC).to_regex()
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200


def parse_package_name(name: str) -> list[str]:
    fields = name.split(".")
    if len(fields) not in (2, 4):
        raise InvalidPackage("Expected two or four package name fields")
    if name.startswith("-") or not all(NAME_PART.fullmatch(part) for part in fields):
        raise InvalidPackage("Package name requires lowercase tokens separated by dots")
    if len(name) > _MAX_NAME_LENGTH:
        raise InvalidPackage("Reference exceeds Conan name constraints")
    return fields


def validate_version_range(version: str) -> None:
    expression = version[1:-1]
    if (
        not (version.startswith("[") and version.endswith("]"))
        or not expression.strip()
        or any(character in expression for character in "#/@[]:%\r\n")
    ):
        raise InvalidPackage("Expected a Conan version range in brackets without a revision")
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
