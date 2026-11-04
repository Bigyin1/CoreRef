"""Immutable snapshots and graph rules; consumer content has no catalog schema."""

from collections import defaultdict
from collections.abc import Collection

import networkx as nx
from frozendict import deepfreeze

from ..api.enums import RelationKind
from ..api.errors import DependencyCycle, IdentityConflict, InvalidPublication
from ..api.models.graph import Relation
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


def normalize_relation(value: Relation) -> Relation:
    if not isinstance(value, Relation):
        raise InvalidPublication("Expected a relation")
    try:
        kind = RelationKind(value.kind)
    except (TypeError, ValueError) as exc:
        raise InvalidPublication(f"Invalid RelationKind: {value.kind!r}") from exc
    if value.ordinal is not None and (type(value.ordinal) is not int or value.ordinal < 0):
        raise InvalidPublication("Ordinal must be a nonnegative integer")
    return Relation(
        normalize_ref(value.source),
        normalize_ref(value.target),
        kind,
        alias=_optional_text(value.alias),
        ordinal=value.ordinal,
        metadata=deepfreeze(value.metadata),
    )


def normalize_query(value: SearchQuery) -> SearchQuery:
    return SearchQuery(
        name=_optional_text(value.name),
        version=_optional_text(value.version),
        kind=_optional_text(value.kind),
        namespace=_optional_text(value.namespace),
    )


def validate(
    records: tuple[Record, ...], relations: tuple[Relation, ...], existing: Collection[Ref]
) -> None:
    if not records:
        raise InvalidPublication("Publication requires records")
    refs = [record.ref for record in records]
    new = set(refs)
    if len(refs) != len(new):
        raise InvalidPublication("Duplicate record identities in batch")
    if new.intersection(existing):
        raise IdentityConflict("Record identity already published")
    _endpoints(relations, new, set(existing))
    _bindings(relations)
    _compatibility(relations)
    _acyclic(relations)


def _endpoints(relations: tuple[Relation, ...], new: set[Ref], existing: set[Ref]) -> None:
    producers: dict[Ref, Ref] = {}
    for edge in relations:
        if edge.source not in new:
            raise InvalidPublication("All outgoing relations must accompany their new source")
        if edge.target not in new | existing:
            raise InvalidPublication("Relation target does not exist")
        if edge.source == edge.target:
            raise InvalidPublication("Self links are forbidden")
        if edge.kind == RelationKind.PRODUCED:
            if edge.target not in new or producers.get(edge.target, edge.source) != edge.source:
                raise InvalidPublication("PRODUCED requires one producer and a new release")
            producers[edge.target] = edge.source


def _bindings(relations: tuple[Relation, ...]) -> None:
    seen: set[str] = set()
    bindings: dict[tuple[Ref, RelationKind, str], set[int | None]] = defaultdict(set)
    for edge in relations:
        encoded = edge.to_json()
        if encoded in seen:
            raise InvalidPublication("Exact duplicate relation")
        seen.add(encoded)
        if edge.alias:
            key = (edge.source, edge.kind, edge.alias)
            ordinals = bindings[key]
            if ordinals and (None in ordinals or edge.ordinal is None or edge.ordinal in ordinals):
                raise InvalidPublication("Repeated alias requires distinct explicit ordinals")
            ordinals.add(edge.ordinal)


def _compatibility(relations: tuple[Relation, ...]) -> None:
    graph: nx.DiGraph[Ref] = nx.DiGraph()
    for edge in relations:
        if edge.kind != RelationKind.BACKWARD_COMPATIBLE_WITH:
            continue
        source, target = edge.source, edge.target
        if source.family != target.family:
            raise InvalidPublication("Compatibility requires versions of the same package")
        if source in graph and graph.out_degree(source):
            raise InvalidPublication("A version can declare only one previous compatible version")
        graph.add_edge(source, target)
    if not nx.is_directed_acyclic_graph(graph):
        raise DependencyCycle("Cycle in BACKWARD_COMPATIBLE_WITH")


def _acyclic(relations: tuple[Relation, ...]) -> None:
    graph: nx.DiGraph[Ref] = nx.DiGraph()
    for edge in relations:
        if edge.kind in (RelationKind.IMPLEMENTS, RelationKind.BACKWARD_COMPATIBLE_WITH):
            continue
        source, target = edge.source, edge.target
        if edge.kind == RelationKind.PRODUCED:
            source, target = target, source
        graph.add_edge(source, target)
    if not nx.is_directed_acyclic_graph(graph):
        raise DependencyCycle("Cycle in REQUIRES/USED/reversed PRODUCED")
