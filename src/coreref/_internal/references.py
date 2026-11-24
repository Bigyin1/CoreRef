"""Validate the package names and ranges accepted in a CoreRef manifest."""

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from ..api.errors import InvalidPackage

_NAME = re.compile(r"[a-z0-9_][a-z0-9_-]*")
_VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{0,100}")
_MAX_NAME_LENGTH = 101


def parse_package_name(name: str) -> tuple[str, str]:
    namespace, _, component = name.partition(".")
    if (
        not (_NAME.fullmatch(namespace) and _NAME.fullmatch(component))
        or len(name) > _MAX_NAME_LENGTH
    ):
        raise InvalidPackage("Expected lowercase namespace.component")
    return namespace, component


def validate_version(version: str) -> None:
    if not _VERSION.fullmatch(version):
        raise InvalidPackage(f"Invalid component version: {version!r}")


def requirement_name(requirement: str) -> str:
    """Validate a dependency declaration and return its component name."""
    name, _, version = requirement.partition("/")
    parse_package_name(name)
    if version.startswith("["):
        version_range(version)
    else:
        validate_version(version)
    return name


def version_range(version: str) -> SpecifierSet:
    if not version.startswith("[") or not version.endswith("]") or not version[1:-1].strip():
        raise InvalidPackage("Expected a version range in brackets")
    try:
        return SpecifierSet(",".join(version[1:-1].replace(",", " ").split()))
    except InvalidSpecifier as error:
        raise InvalidPackage(f"Invalid version range: {version}") from error
