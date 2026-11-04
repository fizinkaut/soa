"""create_bookings

Revision ID: 52b3d0f6f672
Revises:
Create Date: 2026-11-04 17:17:03.771636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '52b3d0f6f672'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade():
    op.create_table(
        "bookings",

        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
        ),

        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("flight_id", sa.BigInteger(), nullable=False),

        sa.Column("passenger_name", sa.String(255), nullable=False),
        sa.Column("passenger_email", sa.String(255), nullable=False),

        sa.Column("seat_count", sa.Integer(), nullable=False),
        sa.Column("total_price", sa.Numeric(12, 2), nullable=False),

        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="CONFIRMED",
        ),

        sa.CheckConstraint(
            "seat_count > 0",
            name="valid_booking_seats",
        ),

        sa.CheckConstraint(
            "total_price > 0",
            name="valid_total_price",
        ),

        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'CANCELLED')",
            name="valid_booking_status",
        ),
    )


def downgrade():
    op.drop_table("bookings")
