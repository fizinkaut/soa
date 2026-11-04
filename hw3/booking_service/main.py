import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from threading import Event, Thread

import grpc
import psycopg
from fastapi import FastAPI, HTTPException
from google.protobuf.json_format import MessageToDict
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from booking_service.compensations import DATABASE_URL, release, run_worker, save_job
from booking_service.flight_client import call, client
from proto import flight_pb2

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    worker = Thread(target=run_worker, args=(stop,), daemon=True)
    worker.start()
    yield
    stop.set()
    worker.join(timeout=6)


app = FastAPI(title="Booking Service", lifespan=lifespan)


class CreateBookingRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    flight_id: int = Field(gt=0)
    passenger_name: str = Field(min_length=1, max_length=255)
    passenger_email: str = Field(min_length=1, max_length=255)
    seat_count: int = Field(gt=0, le=2147483647)


def grpc_to_http(exc):
    status = {
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.RESOURCE_EXHAUSTED: 409,
        grpc.StatusCode.ALREADY_EXISTS: 409,
        grpc.StatusCode.FAILED_PRECONDITION: 409,
        grpc.StatusCode.UNAUTHENTICATED: 502,
        grpc.StatusCode.UNAVAILABLE: 503,
        grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    }.get(exc.code(), 502)
    raise HTTPException(status_code=status, detail=exc.details())


def serialize_booking(row):
    return {**row, "id": str(row["id"]), "total_price": str(row["total_price"])}


def serialize_flight(flight):
    return MessageToDict(flight, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)


@app.get("/flights")
def search_flights(origin: str, destination: str, date: date | None = None):
    request = flight_pb2.SearchFlightsRequest(origin=origin, destination=destination)
    if date is not None:
        request.date = date.isoformat()
    try:
        response = call(client.SearchFlights, request)
    except grpc.RpcError as exc:
        grpc_to_http(exc)
    return [serialize_flight(flight) for flight in response.flights]


@app.get("/flights/{flight_id}")
def get_flight(flight_id: int):
    try:
        response = call(client.GetFlight, flight_pb2.GetFlightRequest(flight_id=flight_id))
    except grpc.RpcError as exc:
        grpc_to_http(exc)
    return serialize_flight(response.flight)


@app.post("/bookings", status_code=201)
def create_booking(data: CreateBookingRequest):
    booking_id = uuid.uuid4()
    try:
        flight = call(client.GetFlight, flight_pb2.GetFlightRequest(flight_id=data.flight_id)).flight
        price = Decimal(flight.price)
        # The intent survives both a lost RPC response and a Booking Service restart.
        save_job(booking_id, "create")
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT booking_id FROM reservation_compensations WHERE booking_id = %s FOR UPDATE",
                    (booking_id,),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(409, "Booking attempt expired; please submit again")
                call(client.ReserveSeats, flight_pb2.ReserveSeatsRequest(
                    flight_id=data.flight_id, booking_id=str(booking_id), seat_count=data.seat_count,
                ))
                cursor.execute(
                    """INSERT INTO bookings
                       (id, user_id, flight_id, passenger_name, passenger_email, seat_count, total_price, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'CONFIRMED') RETURNING *""",
                    (booking_id, data.user_id, data.flight_id, data.passenger_name,
                     data.passenger_email, data.seat_count, price * data.seat_count),
                )
                booking = cursor.fetchone()
                cursor.execute("DELETE FROM reservation_compensations WHERE booking_id = %s", (booking_id,))
    except grpc.RpcError as exc:
        # A worker will release a reservation even if its response was lost.
        grpc_to_http(exc)
    except psycopg.Error:
        logger.exception("Saving booking %s failed; compensation is persisted", booking_id)
        raise HTTPException(503, "Could not save booking; reserved seats will be returned automatically")
    return serialize_booking(booking)


@app.get("/bookings")
def list_bookings(user_id: str):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()
    return [serialize_booking(row) for row in rows]


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: uuid.UUID):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        booking = conn.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,)).fetchone()
    if booking is None:
        raise HTTPException(404, "Booking not found")
    return serialize_booking(booking)


@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: uuid.UUID):
    booking = get_booking(booking_id)
    if booking["status"] == "CANCELLED":
        return {"id": str(booking_id), "status": "CANCELLED"}
    save_job(booking_id, "cancel")
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT booking_id FROM reservation_compensations WHERE booking_id = %s FOR UPDATE", (booking_id,))
                cursor.execute("SELECT * FROM bookings WHERE id = %s FOR UPDATE", (booking_id,))
                booking = cursor.fetchone()
                if booking["status"] != "CANCELLED":
                    if booking["status"] != "CONFIRMED":
                        raise HTTPException(409, "Booking cannot be cancelled")
                    release(booking_id)
                    cursor.execute("UPDATE bookings SET status = 'CANCELLED' WHERE id = %s", (booking_id,))
                cursor.execute("DELETE FROM reservation_compensations WHERE booking_id = %s", (booking_id,))
    except grpc.RpcError as exc:
        grpc_to_http(exc)
    except psycopg.Error:
        logger.exception("Cancellation deferred for %s", booking_id)
        raise HTTPException(503, "Cancellation will finish automatically when services recover")
    return {"id": str(booking_id), "status": "CANCELLED"}
