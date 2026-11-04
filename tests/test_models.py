"""One reference and record, with opaque consumer-defined content."""

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, replace
from enum import StrEnum
from typing import cast

import pytest

from hwcatalog._internal.validation import normalize_record, normalize_relation
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import RelationKind
from hwcatalog.api.errors import IdentityConflict, InvalidPublication
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.protocols import JSONSerializable
from hwcatalog.api.types import JSON


@dataclass
class UserPayload:
    data: JSON
    calls: int = 0

    def to_json(self) -> str:
        self.calls += 1
        return json.dumps(self.data)


def test_minimal_record() -> None:
    record = Record(Ref("core", "1", "release", "demo"))
    assert record.payload is None
    assert Record.from_json(record.to_json()) == record
    with pytest.raises(FrozenInstanceError):
        record.payload = 1  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, True, 12, "custom", [1, {"size": -10}], {"schema": False}])
def test_any_json_payload_roundtrips(catalog: Catalog, value: JSON) -> None:
    record = Record(Ref("opaque", "1", "release", "demo"), UserPayload(value))
    catalog.publish((record,))
    stored = catalog.get(record.ref)
    assert stored == Record.from_json(record.to_json())
    assert not isinstance(stored.payload, JSONSerializable)


def test_custom_payload_is_serialized_once_and_frozen(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"ports": [2, 3]}]
    payload = UserPayload({"nested": nested})
    catalog.publish((Record(Ref("core", "1", "release", "demo"), payload),))
    assert payload.calls == 1
    nested.append(4)
    stored = catalog.get(Ref("core", "1", "release", "demo"))
    assert isinstance(stored.payload, Mapping)
    assert stored.payload["nested"] == (1, {"ports": (2, 3)})
    with pytest.raises(TypeError):
        stored.payload["new"] = 1  # type: ignore[index]
    assert Record.from_json(stored.to_json()) == stored


def test_invalid_custom_payload_does_not_publish_any_records(catalog: Catalog) -> None:
    class InvalidPayload:
        def to_json(self) -> str:
            return "{"

    with pytest.raises(json.JSONDecodeError):
        catalog.publish(
            (
                Record(Ref("valid", "1", "release", "demo")),
                Record(Ref("invalid", "1", "release", "demo"), InvalidPayload()),
            )
        )
    assert not catalog.search()


def test_namespace_is_part_of_identity(catalog: Catalog) -> None:
    ref = Ref("core", "1", "release", "rtl/ip")
    same = Ref("core", "1", "release", "rtl/ip")
    other = replace(ref, namespace="backend/ip")
    assert ref == same
    assert hash(ref) == hash(same)
    assert ref.family == replace(ref, version="2").family
    assert ref != other
    assert ref.family != other.family
    catalog.publish((Record(ref), Record(other)))
    assert catalog.get(same).ref == ref
    with pytest.raises(IdentityConflict):
        catalog.publish((Record(same),))


@pytest.mark.parametrize(
    "ref",
    [
        Ref("", "1", "release", "demo"),
        Ref("core", " ", "release", "demo"),
        Ref("core", "1", "", "demo"),
        Ref("core", "1", " ", "demo"),
        Ref("core", "1", "release", ""),
        Ref("core", "1", "release", " "),
    ],
)
def test_required_reference_fields(ref: Ref) -> None:
    with pytest.raises(InvalidPublication):
        normalize_record(Record(ref))


class CustomKind(StrEnum):
    REPORT = "vendor.report"


@pytest.mark.parametrize("kind", ["artifact", "execution", CustomKind.REPORT, "custom"])
def test_consumer_kinds(catalog: Catalog, kind: str) -> None:
    ref = Ref("core", "1", kind, "demo")
    catalog.publish((Record(ref),))
    stored = catalog.get(ref)
    assert stored.ref.kind == kind
    assert type(stored.ref.kind) is str


def test_kind_is_required() -> None:
    with pytest.raises(TypeError):
        Ref("core", "1", namespace="demo")  # type: ignore[call-arg]


def test_namespace_is_required() -> None:
    with pytest.raises(TypeError):
        Ref("core", "1", "artifact")  # type: ignore[call-arg]


@pytest.mark.parametrize("kind", ["unknown", 12])
def test_invalid_relation_kind(kind: str | int) -> None:
    with pytest.raises(InvalidPublication):
        normalize_relation(
            Relation(
                Ref("a", "1", "release", "demo"),
                Ref("b", "1", "release", "demo"),
                cast("RelationKind", kind),
            )
        )


@pytest.mark.parametrize("ordinal", [True, -1, "1"])
def test_relation_bindings_remain_structural(ordinal: bool | int | str) -> None:
    edge = Relation(
        Ref("a", "1", "release", "demo"),
        Ref("b", "1", "release", "demo"),
        RelationKind.REQUIRES,
        ordinal=cast("int", ordinal),
    )
    with pytest.raises(InvalidPublication):
        normalize_relation(edge)


def test_empty_batch(catalog: Catalog) -> None:
    with pytest.raises(InvalidPublication):
        catalog.publish(())
