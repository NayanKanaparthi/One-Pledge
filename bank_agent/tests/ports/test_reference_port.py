"""`FacilityRepository`: lenders and facilities, written only by reference seeding as the owner role."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.records import lei
from onepledge_bank_agent.application.ports import Upserted
from onepledge_bank_agent.domain.entities import Lender
from onepledge_bank_agent.domain.enums import FacilityStatus
from onepledge_bank_agent.domain.errors import ReferenceDataError
from onepledge_bank_agent.domain.values import Lei


class TestFacilities:
    """`lenders` and `facilities`."""

    def test_a_facility_reads_back_with_its_lender(self, ports: PortWorld) -> None:
        with ports.units() as unit:
            found = unit.facilities.get(ports.facility.facility_id)
            assert unit.facilities.get(uuid.uuid4()) is None
        assert found is not None
        assert (found.facility, found.lender) == (ports.facility, ports.lender)

    def test_an_upsert_says_whether_it_inserted_updated_or_changed_nothing(self, ports: PortWorld) -> None:
        other = Lender(uuid.uuid4(), "Coastal Motor Credit Inc", Lei(lei("00000ONEPLEDGECOA1")))
        renamed = replace(ports.lender, legal_name="Summit Auto Finance Holdings LLC")
        with ports.backend.owner_units() as unit:
            assert unit.facilities.upsert_lender(ports.lender) is Upserted.UNCHANGED
            assert unit.facilities.upsert_lender(other) is Upserted.INSERTED
            assert unit.facilities.upsert_lender(renamed) is Upserted.UPDATED
            assert unit.facilities.upsert_facility(ports.facility) is Upserted.UNCHANGED
            assert (
                unit.facilities.upsert_facility(replace(ports.facility, name="New name")) is Upserted.UPDATED
            )
            unit.commit()
        with ports.units() as unit:
            found = unit.facilities.get(ports.facility.facility_id)
        assert found is not None
        assert (found.lender.legal_name, found.facility.name) == (
            "Summit Auto Finance Holdings LLC",
            "New name",
        )

    def test_an_upsert_keeps_the_stored_status_and_a_new_facility_keeps_its_own(
        self, ports: PortWorld
    ) -> None:
        suspended = replace(ports.facility, status=FacilityStatus.SUSPENDED)
        new = replace(ports.new_facility("HARBOR-THIRD"), status=FacilityStatus.SUSPENDED)
        with ports.backend.owner_units() as unit:
            assert unit.facilities.upsert_facility(suspended) is Upserted.UNCHANGED
            assert unit.facilities.upsert_facility(new) is Upserted.INSERTED
            unit.commit()
        with ports.units() as unit:
            kept = unit.facilities.get(ports.facility.facility_id)
            inserted = unit.facilities.get(new.facility_id)
        assert kept is not None and kept.facility.status is FacilityStatus.ACTIVE
        assert inserted is not None and inserted.facility.status is FacilityStatus.SUSPENDED

    @pytest.mark.parametrize("clash", ["lei", "legal_name"])
    def test_a_lender_under_another_id_with_a_stored_lei_or_name_is_refused(
        self, ports: PortWorld, clash: str
    ) -> None:
        fresh = Lender(uuid.uuid4(), "Unrelated Lender LLC", Lei(lei("00000ONEPLEDGEUNR1")))
        twin = (
            replace(fresh, lei=ports.lender.lei)
            if clash == "lei"
            else replace(fresh, legal_name=ports.lender.legal_name)
        )
        with pytest.raises(ReferenceDataError), ports.backend.owner_units() as unit:
            unit.facilities.upsert_lender(twin)

    def test_a_facility_under_another_id_with_a_stored_code_is_refused(self, ports: PortWorld) -> None:
        twin = replace(ports.new_facility(ports.facility.code), name="Twin line")
        with pytest.raises(ReferenceDataError), ports.backend.owner_units() as unit:
            unit.facilities.upsert_facility(twin)
        with ports.units() as unit:
            assert unit.facilities.get(twin.facility_id) is None
