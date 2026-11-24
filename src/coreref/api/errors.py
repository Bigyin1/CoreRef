"""Exceptions shared by CoreRef catalog, package and graph operations.

Catch ``CatalogError`` at a command boundary. Use its subclasses to distinguish
missing data from invalid metadata. An underlying exception may be available
through ``__cause__``.
"""


class CatalogError(Exception):
    """Report a failure while reading, resolving or publishing catalog data."""


class NotFound(CatalogError):
    """Report an unavailable component, source tree or artifact."""


class InvalidPackage(CatalogError):
    """Report invalid package coordinates, metadata or graph inputs."""


__all__ = ["CatalogError", "InvalidPackage", "NotFound"]
