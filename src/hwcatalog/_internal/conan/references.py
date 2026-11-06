"""Pure conversions between catalog coordinates and Conan references."""

import re
from typing import TYPE_CHECKING

from ...api.errors import InvalidPackage
from ...api.query import SearchQuery
from ...api.records import Ref

if TYPE_CHECKING:
    from ...api.catalog import Catalog

_TOKEN = re.compile(r"[a-z0-9_-]+\Z")
_VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{1,100}\Z")
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200


def to_conan_reference(ref: Ref) -> str:
    """Encode a reference, rejecting names that cannot be represented without loss."""
    if ref.namespace.startswith("-") or not all(
        _TOKEN.fullmatch(value) for value in (ref.namespace, ref.component, ref.view, ref.type)
    ):
        raise InvalidPackage("Conan coordinates require lowercase tokens without dots or slashes")
    name = ref.package_name
    value = str(ref)
    if (
        len(name) > _MAX_NAME_LENGTH
        or len(value) > _MAX_REFERENCE_LENGTH
        or not _VERSION.fullmatch(ref.version)
    ):
        raise InvalidPackage("Reference exceeds Conan name/version constraints")
    return value


def from_conan_reference(value: str, *, catalog: Catalog) -> Ref:
    """Decode a recipe reference; revisions and timestamps are transport details."""
    try:
        name, version = value.split("#", 1)[0].split("/")
        namespace, component, view, package_type = name.split(".")
        ref = Ref(namespace, component, view, package_type, version, catalog=catalog)
        if to_conan_reference(ref) != value.split("#", 1)[0]:
            raise ValueError("Noncanonical coordinates")
        return ref
    except (ValueError, InvalidPackage) as exc:
        raise InvalidPackage(f"Not a catalog Conan reference: {value!r}") from exc


def search_pattern(query: SearchQuery) -> str | None:
    """Use wildcards only for unspecified fields, never for literal query text."""
    fields = (query.namespace, query.component, query.view, query.type)
    if any(value is not None and not _TOKEN.fullmatch(value) for value in fields):
        return None
    if query.version is not None and not _VERSION.fullmatch(query.version):
        return None
    name = ".".join(value if value is not None else "*" for value in fields)
    return f"{name}/{query.version if query.version is not None else '*'}#*"
