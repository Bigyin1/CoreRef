"""Stable domain errors; driver failures never masquerade as missing data."""


class CatalogError(Exception):
    """Base catalog failure."""


class NotFound(CatalogError):
    """An exact identity does not exist."""


class IdentityConflict(CatalogError):
    """A record with this identity already exists."""


class InvalidPublication(CatalogError):
    """A model or publication violates the contract."""


class DependencyCycle(InvalidPublication):
    """A dependency cycle exists."""


class VersionConflict(CatalogError):
    """Dependency requirements could not be resolved to one version per family."""


class BackendUnavailable(CatalogError):
    """The storage could not complete an operation."""


class InvalidPackage(CatalogError):
    """A stored Conan recipe violates the catalog package format."""


class MaterializationError(CatalogError):
    """A resource could not be made available locally or its cache is invalid."""


__all__ = [
    "BackendUnavailable",
    "CatalogError",
    "DependencyCycle",
    "IdentityConflict",
    "InvalidPackage",
    "InvalidPublication",
    "MaterializationError",
    "NotFound",
    "VersionConflict",
]
