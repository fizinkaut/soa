"""create_flights

Revision ID: 909651a04bd1
Revises:
Create Date: 2026-11-04 14:23:26.373650

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '909651a04bd1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade():
    op.create_table(
        "flights",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("flight_number", sa.String(20), nullable=False),
        sa.Column("airline", sa.String(100), nullable=False),
        sa.Column("origin", sa.CHAR(3), nullable=False),
        sa.Column("destination", sa.CHAR(3), nullable=False),
        sa.Column("departure_date", sa.Date(), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_seats", sa.Integer(), nullable=False),
        sa.Column("available_seats", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="SCHEDULED",
        ),
        sa.UniqueConstraint(
            "flight_number",
            "departure_date",
            name="unique_flight",
        ),
        sa.CheckConstraint(
            "total_seats > 0 AND available_seats BETWEEN 0 AND total_seats",
            name="valid_seats",
        ),
        sa.CheckConstraint("price > 0", name="valid_price"),
        sa.CheckConstraint(
            "arrival_at > departure_at",
            name="valid_times",
        ),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')",
            name="valid_status",
        ),
    )


def downgrade():
    op.drop_table("flights")
