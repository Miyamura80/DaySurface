"""Structured error responses and request-ID propagation."""

import json
import uuid
from typing import Any

from fastapi import Request, Response
from loguru import logger as log
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from api_server.billing.stripe_config import reset_stripe_on_auth_error

# Map HTTP status codes to error code strings
_STATUS_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


def _error_code(status: int) -> str:
    return _STATUS_CODE_MAP.get(status, "error")


try:
    from stripe import AuthenticationError as _StripeAuthError
    from stripe import StripeError as _StripeError
except ImportError:
    _StripeError = None  # ty: ignore[invalid-assignment]
    _StripeAuthError = None  # ty: ignore[invalid-assignment]


def _is_stripe_error(exc: Exception) -> bool:
    """Check if an exception originates from Stripe."""
    if _StripeError is not None and isinstance(exc, _StripeError):
        return True
    return type(exc).__module__.startswith("stripe")


def _is_stripe_auth_error(exc: Exception) -> bool:
    """Check if an exception is a Stripe AuthenticationError."""
    if _StripeAuthError is not None and isinstance(exc, _StripeAuthError):
        return True
    return _is_stripe_error(exc) and type(exc).__name__ == "AuthenticationError"


def _build_error_response(
    status_code: int,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    code_override: str | None = None,
) -> JSONResponse:
    error_payload: dict[str, Any] = {
        "code": code_override or _error_code(status_code),
        "message": message,
        "request_id": request_id,
    }
    if details:
        error_payload["details"] = details
    body: dict[str, Any] = {"error": error_payload}
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject ``X-Request-ID`` from the incoming header or generate a uuid4."""

    _MAX_ID_LEN = 128
    _SAFE_CHARS = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    )

    @classmethod
    def _sanitize_request_id(cls, raw: str) -> str:
        """Truncate and strip unsafe characters from a client-supplied ID."""
        return "".join(c for c in raw[: cls._MAX_ID_LEN] if c in cls._SAFE_CHARS)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        raw_id = request.headers.get("X-Request-ID", "")
        request_id = self._sanitize_request_id(raw_id) if raw_id else uuid.uuid4().hex
        if not request_id:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Error-handler middleware
# ---------------------------------------------------------------------------


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catch exceptions and return a consistent JSON error envelope."""

    @staticmethod
    async def _rewrite_error_response(response: Response, request_id: str) -> Response:
        """Buffer a JSON error body and wrap it in a consistent envelope."""
        max_error_body = 1 << 20  # 1 MB cap
        body_bytes = b""
        async for chunk in response.body_iterator:  # ty: ignore[unresolved-attribute]
            body_bytes += chunk if isinstance(chunk, bytes) else chunk.encode()
            if len(body_bytes) > max_error_body:
                # Too large to rewrite; drain iterator without buffering
                # to avoid unbounded memory usage, then return a clean
                # error envelope instead of truncated/malformed JSON.
                async for _ in response.body_iterator:  # ty: ignore[unresolved-attribute]
                    pass
                return _build_error_response(
                    response.status_code,
                    "An error occurred",
                    request_id,
                )

        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}

        # If it already has our error envelope, just add the request ID
        if "error" in data and isinstance(data["error"], dict):
            data["error"]["request_id"] = request_id
            fwd_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "content-type")
            }
            return JSONResponse(
                status_code=response.status_code,
                content=data,
                headers=fwd_headers,
            )

        # Convert FastAPI's {"detail": "..."} format
        message = data.get("detail", "An error occurred")
        if isinstance(message, list):
            return _build_error_response(
                response.status_code,
                "Validation error",
                request_id,
                details={"errors": message},
            )
        if isinstance(message, dict):
            domain_code = message.get("code")
            return _build_error_response(
                response.status_code,
                message.get("message", "An error occurred"),
                request_id,
                details={
                    k: v for k, v in message.items() if k not in ("message", "code")
                },
                code_override=domain_code,
            )
        return _build_error_response(response.status_code, str(message), request_id)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        try:
            response = await call_next(request)
            # Convert non-2xx FastAPI/Starlette error responses into structured format.
            # Only buffer JSON responses to avoid breaking streaming endpoints.
            content_type = response.headers.get("content-type", "")
            if response.status_code >= 400 and "application/json" in content_type:
                return await self._rewrite_error_response(response, request_id)

            return response

        except Exception as exc:  # noqa: BLE001
            # Top-level HTTP error boundary: any unhandled exception in the request
            # pipeline must be converted to a structured 5xx response.
            log.exception("Unhandled exception in request {}", request_id)

            # Sanitize Stripe errors
            if _is_stripe_error(exc):
                if _is_stripe_auth_error(exc):
                    reset_stripe_on_auth_error()
                return _build_error_response(
                    502,
                    "Payment processing error",
                    request_id,
                )

            return _build_error_response(
                500,
                "Internal server error",
                request_id,
            )


async def attachment_too_large_handler(request: Request, exc: Exception) -> Response:
    """Map ``GmailAttachmentTooLargeError`` to 413 Payload Too Large.

    Registered on the app so a client requesting an over-cap attachment gets a
    clear client error instead of the generic 500 the middleware would otherwise
    produce for an uncaught domain exception.
    """
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return _build_error_response(413, str(exc), request_id)
