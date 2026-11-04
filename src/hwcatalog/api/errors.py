"""Stable domain errors; driver failures never masquerade as missing data."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.records import Ref


class CatalogError(Exception):
    """Base catalog failure."""


class NotFound(CatalogError):
    """An exact identity does not exist."""


class IdentityConflict(CatalogError):
    """A record with this identity already exists."""


class InvalidPublication(CatalogError):
    """A model or publication violates the contract."""


class DependencyCycle(InvalidPublication):
    """A dependency, compatibility or causal cycle exists."""


class VersionConflict(CatalogError):
    """Dependency requirements could not be resolved to one version per family."""


class BackendUnavailable(CatalogError):
    """The storage could not complete an operation."""


class UnsupportedOperation(CatalogError):
    """The backend cannot provide the requested operation or relation semantics."""


class InvalidPackage(CatalogError):
    """A stored Conan recipe violates the catalog package format."""


class PartialPublicationError(BackendUnavailable):
    """A nontransactional publication failed; ``uncertain`` may have been written."""

    def __init__(self, published: tuple[Ref, ...], uncertain: Ref) -> None:
        super().__init__(f"Publication interrupted at {uncertain.identity}")
        self.published = published
        self.uncertain = uncertain


class GitResolutionError(CatalogError):
    """A local Git reference could not be resolved to a commit."""


class MaterializationError(CatalogError):
    """A resource could not be made available locally or its cache is invalid."""


__all__ = [
    "BackendUnavailable",
    "CatalogError",
    "DependencyCycle",
    "GitResolutionError",
    "IdentityConflict",
    "InvalidPackage",
    "InvalidPublication",
    "MaterializationError",
    "NotFound",
    "PartialPublicationError",
    "UnsupportedOperation",
    "VersionConflict",
]
