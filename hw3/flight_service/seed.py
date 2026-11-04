"""Add example flights only if the catalogue is empty."""
import os
from datetime import datetime, timedelta, timezone

import psycopg


def seed():
    if os.getenv("SEED_DEMO_FLIGHTS", "false").lower() != "true":
        return
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("LOCK TABLE flights IN EXCLUSIVE MODE")
        if conn.execute("SELECT 1 FROM flights LIMIT 1").fetchone():
            return
        for number, origin, destination, price, offset in [
            ("SU1234", "SVO", "LED", "5000.00", 0),
            ("SU1236", "SVO", "LED", "6500.00", 3),
            ("DP201", "VKO", "KZN", "4200.00", 1),
        ]:
            departure = tomorrow + timedelta(hours=offset)
            conn.execute(
                """INSERT INTO flights
                   (flight_number, airline, origin, destination, departure_date,
                    departure_at, arrival_at, total_seats, available_seats, price)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 100, 100, %s)""",
                (number, "Demo Airlines", origin, destination, departure.date(),
                 departure, departure + timedelta(hours=2), price),
            )
        # Seeding is also a mutation of cached search results.
        conn.execute("UPDATE cache_state SET revision = revision + 1 WHERE id = 1")
    print("Added three demo flights", flush=True)


if __name__ == "__main__":
    seed()
