"""reference.json of SPEC 5.3: banks, lenders, facilities and demo users (no passwords)."""

from __future__ import annotations

from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import DemoUser
from onepledge_datagen.domain.randomness import SeededStreams

ROLES: tuple[tuple[str, str, str], ...] = (
    ("analyst", "ANALYST", "Analyst"),
    ("officer", "CREDIT_OFFICER", "Credit Officer"),
    ("admin", "ADMIN", "Administrator"),
)


def demo_users(bank_codes: tuple[str, ...]) -> list[DemoUser]:
    """Three users per bank: `analyst@`, `officer@` and `admin@<bank>.example`."""
    users = []
    for bank in catalog.BANKS:
        if bank.code not in bank_codes:
            continue
        for local, role, title in ROLES:
            users.append(
                DemoUser(bank.code, f"{local}@{bank.code.lower()}.example", f"{bank.name} {title}", role)
            )
    return users


class ReferenceBuilder:
    """Reference data restricted to the facilities a profile uses, with seeded ids."""

    def __init__(self, streams: SeededStreams) -> None:
        self._rng = streams.stream("reference")

    def build(self, facility_codes: tuple[str, ...]) -> dict[str, object]:
        """The reference document."""
        facilities = [catalog.facility(code) for code in facility_codes]
        bank_codes = tuple(dict.fromkeys(f.bank_code for f in facilities))
        lender_codes = tuple(dict.fromkeys(f.lender_code for f in facilities))
        lender_ids = {lender.code: self._rng.new_uuid() for lender in catalog.LENDERS}
        facility_ids = {facility.code: self._rng.new_uuid() for facility in catalog.FACILITIES}
        return {
            "banks": [
                {"bank_code": b.code, "bank_name": b.name} for b in catalog.BANKS if b.code in bank_codes
            ],
            "lenders": [
                {
                    "lender_id": lender_ids[lender.code],
                    "lender_code": lender.code,
                    "legal_name": lender.legal_name,
                    "lei": lender.lei,
                }
                for lender in catalog.LENDERS
                if lender.code in lender_codes
            ],
            "facilities": [
                {
                    "facility_id": facility_ids[f.code],
                    "facility_code": f.code,
                    "bank_code": f.bank_code,
                    "lender_code": f.lender_code,
                    "name": f.name,
                    "credit_limit_cents": f.credit_limit_cents,
                    "advance_rate_bps": f.advance_rate_bps,
                    "opened_on": f.opened_on.isoformat(),
                }
                for f in facilities
            ],
            "demo_users": [
                {"bank_code": u.bank_code, "email": u.email, "display_name": u.display_name, "role": u.role}
                for u in demo_users(bank_codes)
            ],
        }
