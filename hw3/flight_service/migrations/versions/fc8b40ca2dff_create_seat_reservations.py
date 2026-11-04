"""create_seat_reservations

Revision ID: fc8b40ca2dff
Revises: 909651a04bd1
Create Date: 2026-11-04 16:41:09.831639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fc8b40ca2dff'
down_revision: Union[str, Sequence[str], None] = '909651a04bd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade():
    op.create_table(
        "seat_reservations",

        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),

        sa.Column(
            "flight_id",
            sa.BigInteger(),
            sa.ForeignKey("flights.id"),
            nullable=False,
        ),

        sa.Column(
            "booking_id",
            sa.UUID(),
            nullable=False,
            unique=True,
        ),

        sa.Column(
            "seat_count",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="ACTIVE",
        ),

        sa.CheckConstraint(
            "seat_count > 0",
            name="valid_reservation_seats",
        ),

        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RELEASED', 'EXPIRED')",
            name="valid_reservation_status",
        ),
    )


def downgrade():
    op.drop_table("seat_reservations")
