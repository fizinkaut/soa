"""Cache revision and cancellation markers.

Revision ID: a001_cache_state
Revises: fc8b40ca2dff
"""
from alembic import op
import sqlalchemy as sa

revision = "a001_cache_state"
down_revision = "fc8b40ca2dff"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cache_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1 AND revision > 0", name="valid_cache_state"),
    )
    op.execute("INSERT INTO cache_state (id) VALUES (1)")
    # A cancelled UUID cannot be reserved by a delayed RPC later.
    op.create_table("cancelled_reservations", sa.Column("booking_id", sa.UUID(), primary_key=True))


def downgrade():
    op.drop_table("cancelled_reservations")
    op.drop_table("cache_state")
