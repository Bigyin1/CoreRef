"""Pure conversions between catalog coordinates and Conan references."""

from typing import TYPE_CHECKING

from edify import RegexBuilder

from ..api.errors import InvalidPackage
from ..api.records import Ref

if TYPE_CHECKING:
    from ..api.catalog import Catalog

_ALPHANUMERIC = RegexBuilder().any_of().range("a", "z").range("0", "9").end()
_COMPONENT_CHARACTER = RegexBuilder().any_of().use(_ALPHANUMERIC).char("-").end()
_NAME_CHARACTER = RegexBuilder().any_of().use(_ALPHANUMERIC).any_of_chars("_-").end()
_VERSION_START = RegexBuilder().any_of().use(_ALPHANUMERIC).char("_").end()
_VERSION_CHARACTER = RegexBuilder().any_of().use(_ALPHANUMERIC).any_of_chars("_+.-").end()

COMPONENT_PART = RegexBuilder().one_or_more().use(_COMPONENT_CHARACTER).to_regex().compiled
NAME_PART = RegexBuilder().one_or_more().use(_NAME_CHARACTER).to_regex().compiled
VERSION = (
    RegexBuilder().use(_VERSION_START).between(1, 100).use(_VERSION_CHARACTER).to_regex().compiled
)
REVISION = RegexBuilder().one_or_more().use(_ALPHANUMERIC).to_regex().compiled
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
    value = str(ref.without_revision())
    if (
        len(name) > _MAX_NAME_LENGTH
        or len(value) > _MAX_REFERENCE_LENGTH
        or not VERSION.fullmatch(ref.version)
    ):
        raise InvalidPackage("Reference exceeds Conan name/version constraints")
    if ref.revision is not None and (
        not REVISION.fullmatch(ref.revision) or ref.revision == "latest"
    ):
        raise InvalidPackage(f"Invalid concrete recipe revision: {ref.revision!r}")
    return str(ref)


def from_conan_reference(value: str, *, catalog: Catalog) -> Ref:
    """Decode coordinates and a recipe revision, omitting transport timestamps."""
    try:
        reference = value.split("%", 1)[0]
        name, version = reference.split("#", 1)[0].split("/")
        namespace, component, view, package_type = name.split(".")
        component, separator, subcomponent = component.partition("_")
        ref = Ref(
            namespace,
            component,
            view,
            package_type,
            version,
            subcomponent=subcomponent if separator else None,
            revision=reference.partition("#")[2] or None,
            catalog=catalog,
        )
        if to_conan_reference(ref) != reference:
            raise ValueError("Noncanonical coordinates")
        return ref
    except (ValueError, InvalidPackage) as exc:
        raise InvalidPackage(f"Not a catalog Conan reference: {value!r}") from exc
