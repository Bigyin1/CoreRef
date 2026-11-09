"""The adapter exposes stable SDK errors while preserving diagnostics as causes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from conan.errors import ConanException  # type: ignore[import-untyped]

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage, NotFound
from examples.conan_config import configured_home


@pytest.mark.parametrize(
    ("operation", "message", "kind", "expected"),
    [
        ("graph", "backend internal path", CatalogError, "Could not resolve dependencies"),
        ("install", "backend internal path", CatalogError, "Could not retrieve package contents"),
        ("workspace", "backend internal path", CatalogError, "Could not update the workspace"),
        ("download", "backend not found", NotFound, "A required package is unavailable"),
        ("graph", "backend not in lockfile", InvalidPackage, "absent from its lockfile"),
    ],
)
def test_command_errors_hide_backend_text_and_keep_cause(
    *, tmp_path: Path, operation: str, message: str, kind: type[CatalogError], expected: str
) -> None:
    catalog = Catalog(configured_home(tmp_path / "home"))
    original = ConanException(message)
    with (
        patch.object(catalog._client, "_execute", side_effect=original),
        pytest.raises(kind, match=expected) as caught,
    ):
        catalog._client.run(operation)
    assert message not in str(caught.value)
    assert caught.value.__cause__ is original
