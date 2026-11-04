
import os
import uuid
import grpc

from proto import flight_pb2, flight_pb2_grpc

channel = grpc.insecure_channel("localhost:50051")
client = flight_pb2_grpc.FlightServiceStub(channel)

metadata = (("x-api-key", os.environ["GRPC_API_KEY"]),)
flight_id = 1
booking_id = str(uuid.uuid4())

def available():
    response = client.GetFlight(
        flight_pb2.GetFlightRequest(flight_id=flight_id), metadata=metadata, timeout=5
    )
    return response.flight.available_seats

print("До бронирования:", available())

client.ReserveSeats(
    flight_pb2.ReserveSeatsRequest(
        flight_id=flight_id,
        booking_id=booking_id,
        seat_count=2,
    ), metadata=metadata, timeout=5
)
print("После бронирования:", available())

response = client.ReleaseReservation(
    flight_pb2.ReleaseReservationRequest(
        booking_id=booking_id
    ), metadata=metadata, timeout=5
)
print("Отмена:", response.released)
print("После отмены:", available())

# Проверка повторной отмены
client.ReleaseReservation(
    flight_pb2.ReleaseReservationRequest(
        booking_id=booking_id
    ), metadata=metadata, timeout=5
)
print("После повторной отмены:", available())
