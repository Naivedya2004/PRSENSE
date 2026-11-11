"""Consistent, safe error responses for the whole API.

Every error body has the shape {"error": {"code": ..., "message": ..., "details": ...}}.
Internal exception text is never copied into the response for unexpected errors.
"""
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework import status


def safe_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Not an APIException: let Django produce its 500 page, which is logged by Django.
        return None

    code = {
        status.HTTP_400_BAD_REQUEST: "validation_error",
        status.HTTP_401_UNAUTHORIZED: "not_authenticated",
        status.HTTP_403_FORBIDDEN: "permission_denied",
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    }.get(response.status_code, "error")

    details = response.data
    message = "Request failed." if code == "error" else "See details."
    if isinstance(details, dict) and "detail" in details and len(details) == 1:
        message = str(details["detail"])
        details = None

    response.data = {"error": {"code": code, "message": message, "details": details}}
    return Response(response.data, status=response.status_code, headers=dict(response.items()) or None)
