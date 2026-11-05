"""In-memory reference storage for records and exact dependencies."""

import hashlib
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import IdentityConflict, InvalidPublication, MaterializationError, NotFound
from ...api.graph import CatalogGraph
from ...api.models.graph import Dependency
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ...api.models.resources import GitResource, MaterializedPackage
from ..manifest import read_manifest
from ..validation import normalize_publication


class MemoryReader:
    def __init__(self, records: dict[Ref, Record], dependencies: tuple[Dependency, ...]) -> None:
        self.records = records
        self.edges = dependencies

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        try:
            return tuple(self.records[ref] for ref in refs)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from exc

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        return self._matching(tuple(self.records.values()), query)

    def dependencies(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        self.get_many((ref,))
        return self._matching(
            tuple(self.records[edge.target] for edge in self.edges if edge.source == ref), filters
        )

    def consumers(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        self.get_many((ref,))
        return self._matching(
            tuple(self.records[edge.source] for edge in self.edges if edge.target == ref), filters
        )

    @staticmethod
    def _matching(records: tuple[Record, ...], filters: SearchQuery) -> tuple[Record, ...]:
        return tuple(
            sorted((r for r in records if filters.matches(r)), key=lambda r: r.ref.identity)
        )


class MemoryBackend:
    def __init__(self) -> None:
        self._content = TemporaryDirectory(prefix="hwcatalog-memory-")
        self._records: dict[Ref, Record] = {}
        self._dependencies: tuple[Dependency, ...] = ()
        self._lock = threading.RLock()
        self.graph = CatalogGraph(self._read)

    @contextmanager
    def _read(self) -> Iterator[MemoryReader]:
        with self._lock:
            yield MemoryReader(self._records, self._dependencies)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.get_many(refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.search(query)

    def publish(
        self,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        _require_no_lockfile(lockfile)
        record, dependencies = normalize_publication(record, dependencies)
        if record.files:
            raise InvalidPublication("Publish stored files using a package JSON document")
        return self._save(record, dependencies, {})

    def publish_file(self, path: Path, *, lockfile: Path | None = None) -> Record:
        _require_no_lockfile(lockfile)
        manifest = read_manifest(path)
        record = manifest.record
        if manifest.source_path is not None:
            record = replace(
                record,
                source=GitResource.from_repository(
                    manifest.source_path, repository_url=str(manifest.source_path)
                ),
            )
        record, dependencies = normalize_publication(record, manifest.dependencies)
        return self._save(record, dependencies, manifest.files)

    def _save(
        self, record: Record, dependencies: tuple[Ref, ...], files: dict[str, Path]
    ) -> Record:
        with self._lock:
            if record.ref in self._records:
                raise IdentityConflict("Record identity already published")
            self.get_many(dependencies)
            self._store_content(record, files)
            self._records[record.ref] = record
            self._dependencies += tuple(Dependency(record.ref, target) for target in dependencies)
            return record

    def create_lockfile(self, refs: tuple[Ref, ...], path: Path) -> Path:
        raise InvalidPublication("Project lockfiles require a Conan backend")

    def materialize(
        self, ref: Ref, *, with_sources: bool = False, lockfile: Path | None = None
    ) -> MaterializedPackage:
        _require_no_lockfile(lockfile)
        with self._lock:
            record = self.get_many((ref,))[0]
            directory = self._content_path(ref)
            if record.source is None and not record.files:
                raise MaterializationError("Package has no source or artifacts")
            directory.mkdir(parents=True, exist_ok=True)
            source_path = None
            if with_sources and record.source is not None:
                source_path = record.source.materialize(self._content.name).path
            return MaterializedPackage(directory, source_path)

    def _store_content(self, record: Record, files: dict[str, Path]) -> None:
        if not files:
            return
        with TemporaryDirectory(dir=self._content.name) as temporary:
            package = Path(temporary) / "package"
            package.mkdir()
            for name, local in files.items():
                destination = package / "artifacts" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local, destination)
            destination = self._content_path(record.ref)
            if destination.exists():
                shutil.rmtree(destination)
            package.rename(destination)

    def _content_path(self, ref: Ref) -> Path:
        return Path(self._content.name) / hashlib.sha256(ref.identity.encode()).hexdigest()

    def close(self) -> None:
        self._content.cleanup()


def _require_no_lockfile(lockfile: Path | None) -> None:
    if lockfile is not None:
        raise InvalidPublication("Project lockfiles require a Conan backend")
