"""API key authentication for every Flight Service RPC."""
import hmac
import os

import grpc


class ApiKeyInterceptor(grpc.ServerInterceptor):
    def __init__(self):
        self.api_key = os.environ["GRPC_API_KEY"]
        if not self.api_key:
            raise ValueError("GRPC_API_KEY must not be empty")

    def intercept_service(self, continuation, handler_call_details):
        supplied_key = dict(handler_call_details.invocation_metadata).get("x-api-key", "")
        if hmac.compare_digest(supplied_key.encode(), self.api_key.encode()):
            return continuation(handler_call_details)

        def reject(request, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or missing API key")

        return grpc.unary_unary_rpc_method_handler(reject)
