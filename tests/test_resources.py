"""Resource helpers are optional consumer codecs, independent of catalog payloads."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest
from pydantic import TypeAdapter

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.models.resources import GitResource, LocatedResource, Resource


def test_resource_is_abstract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        Resource()  # type: ignore[abstract]


def test_resource_fields_cannot_be_reassigned() -> None:
    resource = LocatedResource("nfs", "/data")
    with pytest.raises(FrozenInstanceError):
        resource.provider = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "resource",
    [
        LocatedResource("nfs", "/reports", attributes={"ports": [1, 2]}),
        GitResource("https://example.invalid/repo", "main", "a" * 40),
    ],
)
def test_consumer_decodes_its_own_resource(
    catalog: Catalog, resource: GitResource | LocatedResource
) -> None:
    adapter = TypeAdapter(GitResource | LocatedResource)
    descriptor = adapter.dump_python(resource, mode="json")
    record = Record(
        Ref("resource", "v1", "release", "demo"),
        payload=(descriptor,),
    )
    catalog.publish((record,))
    stored = catalog.get(record.ref)
    assert isinstance(stored.payload, tuple)
    payload = stored.payload[0]
    assert isinstance(payload, Mapping)
    restored = adapter.validate_python(dict(payload))
    assert type(restored) is type(resource)
    assert adapter.dump_python(restored, mode="json") == descriptor
