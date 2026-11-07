"""One canonical text format for public references and native transport adaptation."""

from unittest.mock import patch

import pytest

from coreref._internal.references import from_conan_reference
from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.records import Ref


@pytest.mark.parametrize(
    "value", ["soc.cpu.rtl.default/1.0", "soc.cpu_alu_cluster.be.lib-lef/2.0#abc123"]
)
def test_parse_round_trip_without_conan_calls(catalog: Catalog, value: str) -> None:
    with patch.object(
        catalog._client, "run", side_effect=AssertionError("Parsing must not call Conan")
    ):
        ref = Ref(value, catalog=catalog)
        assert str(ref) == value and ref._catalog is catalog
        assert Ref(str(ref.without_revision()), catalog=catalog) == ref.without_revision()
        assert from_conan_reference(value + "%123.0", catalog=catalog) == ref


@pytest.mark.parametrize(
    "value",
    [
        "",
        "soc.cpu.rtl/1.0",
        "soc.cpu.rtl.default/1.0#",
        "soc.cpu.rtl.default/1.0#latest",
        "soc.cpu.rtl.default/1.0#*",
        "soc.cpu.rtl.default/1.0#abc%123",
        "soc.cpu_.be.lib-lef/1.0",
        "soc.cpu.rtl.default/1.0@user/channel",
    ],
)
def test_parse_rejects_noncanonical_or_floating_revisions(catalog: Catalog, value: str) -> None:
    with pytest.raises(InvalidPackage, match="Expected namespace"):
        Ref(value, catalog=catalog)
