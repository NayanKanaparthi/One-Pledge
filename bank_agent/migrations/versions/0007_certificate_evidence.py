"""A failed certificate stays as evidence beside the verified one that replaces it (SPEC_DECISIONS 61, 64).

`certificates` changes shape: a surrogate primary key `certificate_row_id`;
`certificate_id` and `request_id` are no longer unique on their own; at most one
verified row per certificate and per request (partial unique indexes); the same
delivery is stored once. A delivery is identified by the certificate id, the signature
and `delivered_body_sha256`, the SHA-256 of the canonical JSON of the body as it
arrived, computed by the bank when it stores the row (SPEC_DECISIONS 64). `body_hash`
keeps the hash the registry claimed; the two differ exactly when the body was altered,
which is why the claimed hash is not part of the key: CERTIFICATE_BODY changes the body
without touching the claim or the signature. The table stays insert-only: the statement
trigger of 0002 is untouched, and adding columns rewrites the table without firing it.

This revision was corrected in place by decision 64 (no database outside tests existed).
It applies to a database whose `certificates` table is empty: the bank's canonical hash
cannot be computed in SQL, so stored rows could not be given one.

Revision ID: 0007_certificate_evidence
Revises: 0006_quarantine
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision = "0007_certificate_evidence"
down_revision = "0006_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Surrogate key, delivered body hash, partial unique indexes on verified rows, one row per delivery."""
    held: int = op.get_bind().execute(sa.text("SELECT count(*) FROM certificates")).scalar_one()
    if held:
        raise CommandError(
            "certificates holds rows; 0007 needs an empty table because delivered_body_sha256 is computed "
            "by the bank agent, not in SQL."
        )
    op.execute("ALTER TABLE certificates ADD COLUMN certificate_row_id uuid NOT NULL")
    op.execute("ALTER TABLE certificates DROP CONSTRAINT certificates_pkey")
    op.execute("ALTER TABLE certificates ADD CONSTRAINT certificates_pkey PRIMARY KEY (certificate_row_id)")
    op.execute("ALTER TABLE certificates DROP CONSTRAINT uq_certificates_request_id")
    op.add_column("certificates", sa.Column("delivered_body_sha256", sa.CHAR(64), nullable=False))
    op.create_check_constraint(
        "ck_certificates_delivered_body_sha256", "certificates", "delivered_body_sha256 ~ '^[0-9a-f]{64}$'"
    )
    op.create_unique_constraint(
        "uq_certificates_delivery", "certificates", ["certificate_id", "delivered_body_sha256", "signature"]
    )
    op.create_index(
        "uq_certificates_verified_certificate_id",
        "certificates",
        ["certificate_id"],
        unique=True,
        postgresql_where="verified",
    )
    op.create_index(
        "uq_certificates_verified_request_id",
        "certificates",
        ["request_id"],
        unique=True,
        postgresql_where="verified",
    )
    op.create_index("ix_certificates_request_id", "certificates", ["request_id"])


def downgrade() -> None:
    """The one row per request shape of 0001 (fails while a request has a failed and a verified row)."""
    op.drop_index("ix_certificates_request_id", table_name="certificates")
    op.drop_index("uq_certificates_verified_request_id", table_name="certificates")
    op.drop_index("uq_certificates_verified_certificate_id", table_name="certificates")
    op.drop_constraint("uq_certificates_delivery", "certificates", type_="unique")
    op.drop_constraint("ck_certificates_delivered_body_sha256", "certificates", type_="check")
    op.drop_column("certificates", "delivered_body_sha256")
    op.create_unique_constraint("uq_certificates_request_id", "certificates", ["request_id"])
    op.execute("ALTER TABLE certificates DROP CONSTRAINT certificates_pkey")
    op.execute("ALTER TABLE certificates ADD CONSTRAINT certificates_pkey PRIMARY KEY (certificate_id)")
    op.execute("ALTER TABLE certificates DROP COLUMN certificate_row_id")
