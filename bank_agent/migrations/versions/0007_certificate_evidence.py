"""A failed certificate stays as evidence beside the verified one that replaces it (SPEC_DECISIONS 61).

`certificates` changes shape: a surrogate primary key `certificate_row_id`;
`certificate_id` and `request_id` are no longer unique on their own; at most one
verified row per certificate and per request (partial unique indexes); the same
certificate fetched twice is stored once. A delivery is the id, the body hash and the
signature as received, and the body itself: CERTIFICATE_BODY changes the body without
touching the hash or the signature, so a key without the body would refuse the genuine
certificate fetched after the tamper is undone. `md5(body::text)` stands for the body in
the key (jsonb text is normalised, so equal bodies give equal text). The table
stays insert-only: the statement trigger of 0002 is untouched, and adding a column
rewrites the table without firing it. Existing rows get a random row id.

Revision ID: 0007_certificate_evidence
Revises: 0006_quarantine
"""

from __future__ import annotations

from alembic import op

revision = "0007_certificate_evidence"
down_revision = "0006_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Surrogate key, partial unique indexes on verified rows, one row per delivery."""
    op.execute(
        "ALTER TABLE certificates ADD COLUMN certificate_row_id uuid NOT NULL DEFAULT gen_random_uuid()"
    )
    op.execute("ALTER TABLE certificates ALTER COLUMN certificate_row_id DROP DEFAULT")
    op.execute("ALTER TABLE certificates DROP CONSTRAINT certificates_pkey")
    op.execute("ALTER TABLE certificates ADD CONSTRAINT certificates_pkey PRIMARY KEY (certificate_row_id)")
    op.execute("ALTER TABLE certificates DROP CONSTRAINT uq_certificates_request_id")
    op.execute(
        "CREATE UNIQUE INDEX uq_certificates_delivery ON certificates "
        "(certificate_id, body_hash, signature, md5(body::text))"
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
    op.drop_index("uq_certificates_delivery", table_name="certificates")
    op.create_unique_constraint("uq_certificates_request_id", "certificates", ["request_id"])
    op.execute("ALTER TABLE certificates DROP CONSTRAINT certificates_pkey")
    op.execute("ALTER TABLE certificates ADD CONSTRAINT certificates_pkey PRIMARY KEY (certificate_id)")
    op.execute("ALTER TABLE certificates DROP COLUMN certificate_row_id")
