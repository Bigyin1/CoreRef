"""Pure conversions between catalog coordinates and Conan references."""

import re
from typing import TYPE_CHECKING

from ..api.errors import InvalidPackage
from ..api.records import Ref

if TYPE_CHECKING:
    from ..api.catalog import Catalog

COMPONENT_PART = re.compile(r"[a-z0-9-]+\Z")
NAME_PART = re.compile(r"[a-z0-9_-]+\Z")
VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{1,100}\Z")
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200


def to_conan_reference(ref: Ref) -> str:
    """Encode a reference, rejecting names that cannot be represented without loss."""
    if ref.namespace.startswith("-") or not all(
        NAME_PART.fullmatch(value) for value in (ref.namespace, ref.component, ref.view, ref.type)
    ):
        raise InvalidPackage("Conan coordinates require lowercase tokens without dots or slashes")
    if not COMPONENT_PART.fullmatch(ref.component):
        raise InvalidPackage(
            "Underscore separates component from subcomponent; use hyphens in component"
        )
    if ref.subcomponent is not None and not NAME_PART.fullmatch(ref.subcomponent):
        raise InvalidPackage("Subcomponent must be a nonempty lowercase token")
    name = ref.package_name
    value = str(ref)
    if (
        len(name) > _MAX_NAME_LENGTH
        or len(value) > _MAX_REFERENCE_LENGTH
        or not VERSION.fullmatch(ref.version)
    ):
        raise InvalidPackage("Reference exceeds Conan name/version constraints")
    return value


def from_conan_reference(value: str, *, catalog: Catalog) -> Ref:
    """Decode a recipe reference; revisions and timestamps are transport details."""
    try:
        name, version = value.split("#", 1)[0].split("/")
        namespace, component, view, package_type = name.split(".")
        component, separator, subcomponent = component.partition("_")
        ref = Ref(
            namespace,
            component,
            view,
            package_type,
            version,
            subcomponent=subcomponent if separator else None,
            catalog=catalog,
        )
        if to_conan_reference(ref) != value.split("#", 1)[0]:
            raise ValueError("Noncanonical coordinates")
        return ref
    except (ValueError, InvalidPackage) as exc:
        raise InvalidPackage(f"Not a catalog Conan reference: {value!r}") from exc
