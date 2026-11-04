"""Durable reservation compensation jobs.

Revision ID: b001_compensations
Revises: 52b3d0f6f672
"""
from alembic import op
import sqlalchemy as sa

revision = "b001_compensations"
down_revision = "52b3d0f6f672"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reservation_compensations",
        sa.Column("booking_id", sa.UUID(), primary_key=True),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.CheckConstraint("operation IN ('create', 'cancel')", name="valid_compensation_operation"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("reservation_compensations")
