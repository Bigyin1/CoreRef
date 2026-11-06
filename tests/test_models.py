"""One reference and record, with opaque consumer-defined content."""

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, replace
from enum import StrEnum

import pytest

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage
from hwcatalog.api.protocols import JSONSerializable
from hwcatalog.api.records import Record, Ref
from hwcatalog.api.types import JSON
from tests.helpers import local_record


@dataclass
class UserPayload:
    data: JSON
    calls: int = 0

    def to_json(self) -> str:
        self.calls += 1
        return json.dumps(self.data)


def test_minimal_record(*, catalog: Catalog) -> None:
    record = Record(Ref("demo", "core", "release", "default", "1.0", catalog=catalog))
    assert record.payload is None
    assert (
        Record.from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=catalog) == record
    )
    with pytest.raises(FrozenInstanceError):
        record.payload = 1  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, True, 12, "custom", [1, {"size": -10}], {"schema": False}])
def test_any_json_payload_roundtrips(catalog: Catalog, value: JSON) -> None:
    record = Record(
        Ref("demo", "opaque", "release", "default", "1.0", catalog=catalog), UserPayload(value)
    )
    catalog.publish(local_record(record))
    stored = Ref(**record.ref.to_dict(), catalog=catalog).get()
    assert stored == Record.from_json(
        json.dumps(record.to_dict(), allow_nan=False), catalog=catalog
    )
    assert not isinstance(stored.payload, JSONSerializable)


def test_custom_payload_is_serialized_once_and_frozen(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"ports": [2, 3]}]
    payload = UserPayload({"nested": nested})
    catalog.publish(
        local_record(
            Record(Ref("demo", "core", "release", "default", "1.0", catalog=catalog), payload)
        )
    )
    assert payload.calls == 1
    nested.append(4)
    stored = Ref("demo", "core", "release", "default", "1.0", catalog=catalog).get()
    assert isinstance(stored.payload, Mapping)
    assert stored.payload["nested"] == (1, {"ports": (2, 3)})
    with pytest.raises(TypeError):
        stored.payload["new"] = 1  # type: ignore[index]
    assert (
        Record.from_json(json.dumps(stored.to_dict(), allow_nan=False), catalog=catalog) == stored
    )


def test_invalid_custom_payload_does_not_publish_any_records(catalog: Catalog) -> None:
    class InvalidPayload:
        def to_json(self) -> str:
            return "{"

    with pytest.raises(json.JSONDecodeError):
        catalog.publish(
            local_record(
                Record(
                    Ref("demo", "invalid", "release", "default", "1.0", catalog=catalog),
                    InvalidPayload(),
                )
            )
        )
    assert not catalog.search()


def test_namespace_is_part_of_identity(catalog: Catalog) -> None:
    ref = Ref("rtl-ip", "core", "release", "default", "1.0", catalog=catalog)
    same = Ref("rtl-ip", "core", "release", "default", "1.0", catalog=catalog)
    other = replace(ref, namespace="connection-ip", catalog=catalog)
    assert ref == same
    assert hash(ref) == hash(same)
    assert ref.package_name == replace(ref, version="2.0", catalog=catalog).package_name
    assert ref != other
    assert ref.package_name != other.package_name
    catalog.publish(local_record(Record(ref)))
    catalog.publish(local_record(Record(other)))
    assert Ref(**same.to_dict(), catalog=catalog).get().ref == ref
    with pytest.raises(Conflict):
        catalog.publish(local_record(Record(same)))


@pytest.mark.parametrize(
    "coordinates",
    [
        ("demo", "", "release", "default", "1.0"),
        ("demo", "core", "release", "default", " "),
        ("demo", "core", "", "default", "1.0"),
        ("demo", "core", " ", "default", "1.0"),
        ("", "core", "release", "default", "1.0"),
        (" ", "core", "release", "default", "1.0"),
    ],
)
def test_required_reference_fields(
    coordinates: tuple[str, str, str, str, str], catalog: Catalog
) -> None:
    with pytest.raises(InvalidPackage):
        normalize_record(Record(Ref(*coordinates, catalog=catalog)))


class CustomKind(StrEnum):
    REPORT = "vendor-report"


@pytest.mark.parametrize("kind", ["artifact", "execution", CustomKind.REPORT, "custom"])
def test_consumer_kinds(catalog: Catalog, kind: str) -> None:
    ref = Ref("demo", "core", kind, "default", "1.0", catalog=catalog)
    catalog.publish(local_record(Record(ref)))
    stored = Ref(**ref.to_dict(), catalog=catalog).get()
    assert stored.ref.view == kind
    assert type(stored.ref.view) is str


@pytest.mark.parametrize("field", ["namespace", "component", "view", "type", "version"])
def test_coordinate_fields_are_required(field: str, *, catalog: Catalog) -> None:
    coordinates = {
        "namespace": "demo",
        "component": "core",
        "view": "rtl",
        "type": "default",
        "version": "1.0",
    }
    coordinates.pop(field)
    with pytest.raises(TypeError):
        Ref(**coordinates, catalog=catalog)
