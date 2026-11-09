"""One canonical text format for public references and native transport adaptation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from coreref._internal.references import from_conan_reference
from coreref.api.errors import InvalidPackage
from coreref.api.records import Ref


@pytest.mark.parametrize(
    "value", ["soc.cpu.rtl.default/1.0", "soc.cpu_core.alu_cluster.be.lib-lef/2.0#abc123"]
)
def test_parse_round_trip_without_conan_calls(value: str) -> None:
    with patch("coreref._internal.client.ConanAPI", side_effect=AssertionError("Conan")):
        ref = Ref(value)
        assert str(ref) == value
        assert Ref(str(ref.without_revision())) == ref.without_revision()
        assert from_conan_reference(value + "%123.0") == ref


@pytest.mark.parametrize(
    "value",
    [
        "",
        "soc.cpu.rtl/1.0",
        "soc.cpu.rtl.default/1.0#",
        "soc.cpu.rtl.default/1.0#latest",
        "soc.cpu.rtl.default/1.0#*",
        "soc.cpu.rtl.default/1.0#abc%123",
        "soc.cpu..be.lib-lef/1.0",
        "soc.cpu.alu.be.lib-lef.extra/1.0",
        "soc.cpu.be.lib-lef..corner.tt/1.0",
        "soc.cpu.rtl.default/1.0@user/channel",
    ],
)
def test_parse_rejects_noncanonical_or_floating_revisions(value: str) -> None:
    with pytest.raises(InvalidPackage, match="Expected namespace"):
        Ref(value)


@pytest.mark.parametrize(
    "fields",
    [
        {"component": "cpu.alu"},
        {"namespace": "soc.cpu"},
        {"view": "rtl.default"},
        {"type": "default.extra"},
        {"version": "1.0#abc123"},
    ],
)
def test_from_fields_does_not_reinterpret_embedded_separators(fields: dict[str, str]) -> None:
    coordinates = {
        "namespace": "soc",
        "component": "cpu",
        "view": "rtl",
        "type": "default",
        "version": "1.0",
    }
    with pytest.raises(InvalidPackage):
        Ref.from_fields(**(coordinates | fields))
