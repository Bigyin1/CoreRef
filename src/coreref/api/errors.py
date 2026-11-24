"""Exceptions shared by CoreRef catalog, package and graph operations.

Catch ``CatalogError`` at a command boundary. Use its subclasses when missing
data, conflicting requirements or invalid package metadata need separate
handling. An underlying exception may be available through ``__cause__``.
"""


class CatalogError(Exception):
    """Report a failure while reading, resolving or publishing catalog data."""


class NotFound(CatalogError):
    """Report an unavailable package, recipe revision or binary.

    A valid search with zero matches returns an empty tuple.
    """


class Conflict(CatalogError):
    """Report incompatible package requirements or revisions."""


class InvalidPackage(CatalogError):
    """Report invalid package coordinates, metadata or graph inputs."""


__all__ = ["CatalogError", "Conflict", "InvalidPackage", "NotFound"]
