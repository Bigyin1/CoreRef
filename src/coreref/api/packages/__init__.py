"""Address, search and publish hardware packages.

Use ``Ref`` for concrete versions and ``Requirement`` for dependency ranges.
``Catalog`` works with ``Record`` metadata, ``InstalledRecord`` paths and
``LocalRecord`` publication inputs. ``PackageInput`` and
``ConfiguredPackage`` describe option-dependent binaries.
"""

from .catalog import Catalog
from .query import SearchQuery
from .records import (
    JSON,
    ConfiguredPackage,
    Dependency,
    InstalledRecord,
    LocalRecord,
    PackageInput,
    Record,
    Ref,
    Requirement,
)

__all__ = [
    "JSON",
    "Catalog",
    "ConfiguredPackage",
    "Dependency",
    "InstalledRecord",
    "LocalRecord",
    "PackageInput",
    "Record",
    "Ref",
    "Requirement",
    "SearchQuery",
]
