"""Immutable records and exact, acyclic dependency publications."""

from collections.abc import Collection

import networkx as nx

from ..api.errors import DependencyCycle, IdentityConflict, InvalidPublication
from ..api.models.graph import Dependency
from ..api.models.query import SearchQuery
from ..api.models.records import Record, Ref


def _text(value: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise InvalidPublication("Expected a nonempty string" if not blank else "Expected a string")
    return str(value)


def _optional_text(value: str | None) -> str | None:
    return None if value is None else _text(value, blank=True)


def normalize_ref(value: Ref) -> Ref:
    return Ref(_text(value.name), _text(value.version), _text(value.kind), _text(value.namespace))


def normalize_record(value: Record) -> Record:
    record = Record(normalize_ref(value.ref), value.payload)
    return Record.from_json(record.to_json())


def normalize_dependency(value: Dependency) -> Dependency:
    if not isinstance(value, Dependency):
        raise InvalidPublication("Expected a dependency")
    return Dependency(normalize_ref(value.source), normalize_ref(value.target))


def normalize_query(value: SearchQuery) -> SearchQuery:
    return SearchQuery(
        name=_optional_text(value.name),
        version=_optional_text(value.version),
        kind=_optional_text(value.kind),
        namespace=_optional_text(value.namespace),
    )


def validate(
    records: tuple[Record, ...], dependencies: tuple[Dependency, ...], existing: Collection[Ref]
) -> None:
    if not records:
        raise InvalidPublication("Publication requires records")
    refs = [record.ref for record in records]
    new, previous = set(refs), set(existing)
    if len(refs) != len(new):
        raise InvalidPublication("Duplicate record identities in batch")
    if new.intersection(previous):
        raise IdentityConflict("Record identity already published")
    if len(dependencies) != len(set(dependencies)):
        raise InvalidPublication("Duplicate dependency")
    for edge in dependencies:
        if edge.source not in new:
            raise InvalidPublication("Dependencies must accompany their new source")
        if edge.target not in new | previous:
            raise InvalidPublication("Dependency target does not exist")
        if edge.source == edge.target:
            raise InvalidPublication("Self dependencies are forbidden")
    graph: nx.DiGraph[Ref] = nx.DiGraph((edge.source, edge.target) for edge in dependencies)
    if not nx.is_directed_acyclic_graph(graph):
        raise DependencyCycle("Cycle in dependencies")
