"""Persist cleanup before reserving, so a restart cannot lose the refund task."""
import logging
import os
import threading

import grpc
import psycopg
from psycopg.rows import dict_row

from booking_service.flight_client import call, client
from proto import flight_pb2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://booking_user:booking_password@localhost:5434/booking_db")
logger = logging.getLogger(__name__)


def release(booking_id):
    call(client.ReleaseReservation, flight_pb2.ReleaseReservationRequest(booking_id=str(booking_id)))


def save_job(booking_id, operation):
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "INSERT INTO reservation_compensations (booking_id, operation) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (booking_id, operation),
        )


def process_jobs():
    # Active requests lock their own jobs. SKIP LOCKED leaves those requests alone.
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM reservation_compensations
                   WHERE created_at < now() - interval '30 seconds'
                   ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"""
            )
            job = cursor.fetchone()
            if job is None:
                return
            booking_id = job["booking_id"]
            cursor.execute("SELECT status FROM bookings WHERE id = %s FOR UPDATE", (booking_id,))
            booking = cursor.fetchone()
            if job["operation"] == "cancel" or booking is None:
                release(booking_id)
                if job["operation"] == "cancel" and booking is not None:
                    cursor.execute("UPDATE bookings SET status = 'CANCELLED' WHERE id = %s", (booking_id,))
            cursor.execute("DELETE FROM reservation_compensations WHERE booking_id = %s", (booking_id,))
            logger.info("Completed %s compensation for %s", job["operation"], booking_id)


def run_worker(stop: threading.Event):
    while not stop.wait(5):
        try:
            process_jobs()
        except (psycopg.Error, grpc.RpcError):
            logger.exception("Compensation deferred; the saved job will be tried again")
