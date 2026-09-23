from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class UpstreamUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "An upstream data provider is unavailable. Try again later."
    default_code = "upstream_unavailable"

class UpstreamError(Exception):
    """The upstream API was unreachable or returned an error."""

class NotFoundError(Exception):
    """The requested resource doesn't exist upstream."""

def problem_details_handler(exc, context):
    """Render every API error as RFC 9457 problem details."""
    if isinstance(exc, UpstreamError):
        exc = UpstreamUnavailable()

    response = exception_handler(exc, context)
    if response is None:
        return None  # Not an API error: let Django turn it into a 500.

    body = {
        "type": "about:blank",
        "title": response.status_text,
        "status": response.status_code,
    }
    data = response.data
    if isinstance(data, dict) and set(data) == {"detail"}:
        body["detail"] = data["detail"]          # e.g. NotFound("...")
    elif isinstance(data, list):
        body["detail"] = " ".join(data)          # e.g. ValidationError("...")
    else:
        body["detail"] = "One or more parameters are invalid."
        body["errors"] = data                    # per-field messages
    response.data = body

    # Only relabel JSON responses; the browsable API still needs text/html.
    renderer = getattr(context["request"], "accepted_renderer", None)
    if getattr(renderer, "format", None) == "json":
        response.content_type = "application/problem+json"
    return response