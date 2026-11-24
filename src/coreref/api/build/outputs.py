"""Look up named results in a release index and fetch only selected store paths."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from ..._internal.nix_store import store_entry
from ..errors import InvalidPackage, NotFound
from ..packages.catalog import Catalog
from ..packages.records import Record


class Outputs:
    """Retrieve the named files associated with a release or source dependency."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        build: Callable[[Record, tuple[str, ...]], dict[str, Path]] | None = None,
    ) -> None:
        """Bind the cache, optionally supplying a builder for source dependencies."""
        self._catalog = catalog
        self._build = build

    @staticmethod
    def links(record: Record) -> dict[str, Path] | None:
        """Read the exact store paths from the release metadata without I/O."""
        if not isinstance(record.payload, Mapping):
            return None
        index = record.payload.get("output_index")
        if index is None:
            return None
        if not isinstance(index, Mapping):
            raise InvalidPackage(f"Invalid output index for {record.ref}")
        paths = {}
        for name, value in index.items():
            if not isinstance(value, str):
                raise InvalidPackage(f"Invalid store path for {record.ref}:{name}")
            path = Path(value)
            store_entry(path)
            paths[name] = path
        return paths

    def get(self, record: Record, names: tuple[str, ...]) -> dict[str, Path]:
        """Download exactly the requested outputs, or build them from locked sources."""
        if not names:
            return {}
        index = self.links(record)
        if self._build is not None and (index is None or self._catalog._is_editable(record.ref)):
            return self._build(record, names)
        missing = set(names) - (index or {}).keys()
        if index is None or missing:
            raise NotFound(f"{record.ref} has no outputs {', '.join(sorted(missing))}")
        selected = {name: index[name] for name in names}
        self._catalog._nix.restore(tuple(selected.values()))
        if absent := [name for name, path in selected.items() if not path.is_file()]:
            raise NotFound(f"Missing files for {record.ref}: {', '.join(absent)}")
        return selected
