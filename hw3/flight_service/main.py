import logging
import os
import re
import uuid
from concurrent import futures
from datetime import date

import grpc
import psycopg
from google.protobuf.timestamp_pb2 import Timestamp
from psycopg.rows import dict_row

from flight_service import cache
from flight_service.auth import ApiKeyInterceptor
from proto import flight_pb2, flight_pb2_grpc

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://flight_user:flight_password@localhost:5433/flight_db")
logger = logging.getLogger(__name__)


def to_timestamp(value):
    result = Timestamp()
    result.FromDatetime(value)
    return result


def to_proto_flight(flight):
    return flight_pb2.Flight(
        id=flight["id"], flight_number=flight["flight_number"], airline=flight["airline"],
        origin=flight["origin"], destination=flight["destination"],
        departure_date=flight["departure_date"].isoformat(),
        departure_at=to_timestamp(flight["departure_at"]), arrival_at=to_timestamp(flight["arrival_at"]),
        total_seats=flight["total_seats"], available_seats=flight["available_seats"],
        price=str(flight["price"]), status=flight_pb2.FlightStatus.Value(flight["status"]),
    )


def parse_booking_id(value, context):
    try:
        return uuid.UUID(value)
    except ValueError:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid booking_id")


def lock_booking(cursor, booking_id):
    # Reserve and release for one UUID must run in order, including delayed RPCs.
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (booking_id.int % (2**63),))


class FlightService(flight_pb2_grpc.FlightServiceServicer):
    def GetFlight(self, request, context):
        if request.flight_id <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "flight_id must be positive")
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                key = f"flight:{request.flight_id}:v{cache.revision(cursor)}"
                cached = cache.get(key, flight_pb2.GetFlightResponse)
                if cached is not None:
                    return cached
                cursor.execute("SELECT * FROM flights WHERE id = %s", (request.flight_id,))
                flight = cursor.fetchone()
        if flight is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
        response = flight_pb2.GetFlightResponse(flight=to_proto_flight(flight))
        cache.put(key, response)
        return response

    def SearchFlights(self, request, context):
        if not re.fullmatch(r"[A-Z]{3}", request.origin) or not re.fullmatch(r"[A-Z]{3}", request.destination):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Use three uppercase letters for IATA codes")
        departure_date = None
        if request.HasField("date"):
            try:
                departure_date = date.fromisoformat(request.date)
            except ValueError:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid date format")
        query = "SELECT * FROM flights WHERE origin = %s AND destination = %s AND status = 'SCHEDULED'"
        params = [request.origin, request.destination]
        if departure_date is not None:
            query += " AND departure_date = %s"
            params.append(departure_date)
        query += " ORDER BY departure_at, id"
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                key = f"search:{request.origin}:{request.destination}:{departure_date or 'all'}:v{cache.revision(cursor)}"
                cached = cache.get(key, flight_pb2.SearchFlightsResponse)
                if cached is not None:
                    return cached
                cursor.execute(query, params)
                flights = cursor.fetchall()
        response = flight_pb2.SearchFlightsResponse(flights=[to_proto_flight(f) for f in flights])
        cache.put(key, response)
        return response

    def ReserveSeats(self, request, context):
        if request.flight_id <= 0 or request.seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "flight_id and seat_count must be positive")
        booking_id = parse_booking_id(request.booking_id, context)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                lock_booking(cursor, booking_id)
                cursor.execute("SELECT 1 FROM cancelled_reservations WHERE booking_id = %s", (booking_id,))
                if cursor.fetchone() is not None:
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Reservation was cancelled")
                cursor.execute("SELECT * FROM seat_reservations WHERE booking_id = %s", (booking_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    if existing["flight_id"] != request.flight_id or existing["seat_count"] != request.seat_count:
                        context.abort(grpc.StatusCode.ALREADY_EXISTS, "booking_id has different reservation parameters")
                    if existing["status"] != "ACTIVE":
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Reservation is not active")
                    return flight_pb2.ReserveSeatsResponse(
                        reservation_id=existing["id"], booking_id=str(booking_id), status=flight_pb2.ACTIVE,
                    )
                cursor.execute("SELECT * FROM flights WHERE id = %s FOR UPDATE", (request.flight_id,))
                flight = cursor.fetchone()
                if flight is None:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
                if flight["status"] != "SCHEDULED":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Flight is not scheduled")
                if flight["available_seats"] < request.seat_count:
                    context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Not enough available seats")
                cursor.execute(
                    "UPDATE flights SET available_seats = available_seats - %s WHERE id = %s",
                    (request.seat_count, request.flight_id),
                )
                cursor.execute(
                    "INSERT INTO seat_reservations (flight_id, booking_id, seat_count) VALUES (%s, %s, %s) RETURNING id",
                    (request.flight_id, booking_id, request.seat_count),
                )
                reservation_id = cursor.fetchone()["id"]
                cache.invalidate(cursor)
        return flight_pb2.ReserveSeatsResponse(
            reservation_id=reservation_id, booking_id=str(booking_id), status=flight_pb2.ACTIVE,
        )

    def ReleaseReservation(self, request, context):
        booking_id = parse_booking_id(request.booking_id, context)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                lock_booking(cursor, booking_id)
                cursor.execute(
                    "INSERT INTO cancelled_reservations (booking_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (booking_id,),
                )
                cursor.execute("SELECT * FROM seat_reservations WHERE booking_id = %s", (booking_id,))
                reservation = cursor.fetchone()
                # A marker also prevents a late ReserveSeats after a timed-out request.
                if reservation is None or reservation["status"] == "RELEASED":
                    return flight_pb2.ReleaseReservationResponse(released=True)
                if reservation["status"] != "ACTIVE":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Reservation is not active")
                cursor.execute("SELECT id FROM flights WHERE id = %s FOR UPDATE", (reservation["flight_id"],))
                cursor.execute(
                    "UPDATE flights SET available_seats = available_seats + %s WHERE id = %s",
                    (reservation["seat_count"], reservation["flight_id"]),
                )
                cursor.execute("UPDATE seat_reservations SET status = 'RELEASED' WHERE id = %s", (reservation["id"],))
                cache.invalidate(cursor)
        return flight_pb2.ReleaseReservationResponse(released=True)


def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=[ApiKeyInterceptor()])
    flight_pb2_grpc.add_FlightServiceServicer_to_server(FlightService(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    logger.info("Flight Service listening on port 50051")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
