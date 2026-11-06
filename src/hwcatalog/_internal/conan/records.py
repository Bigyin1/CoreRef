"""Read canonical package records and pinned references from Conan."""

from pathlib import Path

from ...api.errors import Conflict, NotFound
from ...api.protocols import RefCatalog
from ...api.query import SearchQuery
from ...api.records import Record, Ref
from ..validation import normalize_query, normalize_ref
from .client import ConanClient
from .format import (
    from_conan_reference,
    read_recipe,
    search_pattern,
    to_conan_reference,
)


def locate(client: ConanClient, ref: Ref) -> tuple[str, Path]:
    logical = to_conan_reference(ref)
    revisions = client.index(f"{logical}*#*").get(logical)
    if revisions is None or not revisions.revisions:
        raise NotFound(ref)
    if len(revisions.revisions) != 1:
        raise Conflict(f"Multiple recipe revisions for {logical}")
    pinned = f"{logical}#{next(iter(revisions.revisions))}"
    return pinned, client.cache_path(pinned)


def get(client: ConanClient, ref: Ref, *, with_sources: bool = False) -> Record:
    ref = normalize_ref(ref)
    record = read_records(client, (to_conan_reference(ref),), catalog=ref._catalog)[ref]
    if record.files or with_sources:
        from .materialization import materialize

        return materialize(client, record, with_sources=with_sources)
    return record


def read_records(
    client: ConanClient, references: tuple[str, ...], *, catalog: RefCatalog
) -> dict[Ref, Record]:
    records = {}
    for reference in dict.fromkeys(references):
        ref = from_conan_reference(reference, catalog=catalog)
        published, directory = locate(client, ref)
        if "#" in reference and published != reference:
            raise Conflict(f"Locked revision differs from published identity: {reference}")
        records[ref] = read_recipe(directory, ref)[0]
    return records


def search(
    client: ConanClient,
    query: SearchQuery = SearchQuery(),
    *,
    catalog: RefCatalog,
) -> tuple[Record, ...]:
    query = normalize_query(query)
    pattern = search_pattern(query)
    if pattern is None:
        return ()
    candidates = client.index(pattern)
    records = read_records(client, tuple(candidates), catalog=catalog).values()
    return tuple(sorted((r for r in records if query.matches(r)), key=lambda r: r.ref))


def dependencies(
    client: ConanClient, ref: Ref, filters: SearchQuery = SearchQuery()
) -> tuple[Record, ...]:
    ref, filters = normalize_ref(ref), normalize_query(filters)
    _, directory = locate(client, ref)
    requirements = read_recipe(directory, ref)[1].requires
    records = read_records(client, requirements, catalog=ref._catalog).values()
    return tuple(sorted((r for r in records if filters.matches(r)), key=lambda r: r.ref))


def requirement(client: ConanClient, ref: Ref) -> str:
    pinned, directory = locate(client, ref)
    read_recipe(directory, ref)
    return pinned
