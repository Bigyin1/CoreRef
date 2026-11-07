"""Catalog failures with distinct absence, conflict and invalid-package cases."""


class CatalogError(Exception):
    """A catalog operation failed; the message and cause describe the failure."""


class NotFound(CatalogError):
    """An exact identity does not exist."""


class Conflict(CatalogError):
    """Dependency versions or recipe revisions conflict."""


class InvalidPackage(CatalogError):
    """A package description, stored recipe or dependency graph is invalid."""


__all__ = ["CatalogError", "Conflict", "InvalidPackage", "NotFound"]
