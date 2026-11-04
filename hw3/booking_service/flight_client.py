"""Shared authenticated Flight Service client."""
import os

import grpc

from proto import flight_pb2_grpc

api_key = os.environ["GRPC_API_KEY"]
if not api_key:
    raise ValueError("GRPC_API_KEY must not be empty")

channel = grpc.insecure_channel(os.getenv("FLIGHT_ADDRESS", "localhost:50051"))
client = flight_pb2_grpc.FlightServiceStub(channel)
metadata = (("x-api-key", api_key),)


def call(method, request):
    return method(request, timeout=5, metadata=metadata)
