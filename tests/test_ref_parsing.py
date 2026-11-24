"""One canonical text format for public references and exact source revisions."""

from __future__ import annotations

import pytest

from coreref.api.errors import InvalidPackage
from coreref.api.packages.records import Ref


@pytest.mark.parametrize(
    "value",
    [
        "soc.cpu/1.0",
        "soc.cpu_core/2.0#abc123",
        "shared.stdlib/1.0",
        "pdk.techlib-n5/2.0#abc123",
    ],
)
def test_parse_round_trip(value: str) -> None:
    ref = Ref(value)
    assert str(ref) == value
    assert Ref(str(ref.without_revision())) == ref.without_revision()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "soc.cpu.rtl/1.0",
        "soc.cpu.rtl.default/1.0",
        "soc.cpu.alu.be.lib-lef/1.0",
        "shared.stdlib/[>=1.0 <2.0]",
        "soc.cpu/1.0#",
        "soc.cpu/1.0#latest",
        "soc.cpu/1.0#*",
        "soc.cpu/1.0#abc%123",
        "soc.cpu..be.lib-lef/1.0",
        "soc.cpu.alu.be.lib-lef.extra/1.0",
        "soc.cpu.be.lib-lef..corner.tt/1.0",
        "soc.cpu/1.0@user/channel",
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
        {"version": "1.0#abc123"},
    ],
)
def test_from_fields_does_not_reinterpret_embedded_separators(fields: dict[str, str]) -> None:
    coordinates = {
        "namespace": "soc",
        "component": "cpu",
        "version": "1.0",
    }
    with pytest.raises(InvalidPackage):
        Ref.from_fields(**(coordinates | fields))
