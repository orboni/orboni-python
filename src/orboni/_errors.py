from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Type


class OrboniError(Exception):
    """Every failure is an OrboniError.

    A request Orboni refused carries the API's own ``type`` (branch on it, or
    on the subclass, never on ``message``), the ``param`` at fault when there
    is one, and the HTTP status.
    """

    _default_type: ClassVar[str] = "api_error"

    #: The API's error type, e.g. ``invalid_request_error``; ``connection_error``
    #: or ``signature_verification_error`` for the library's own.
    type: str
    message: str
    #: The parameter at fault, when there is one.
    param: Optional[str]
    #: The HTTP status; None when no answer came.
    http_status: Optional[int]
    #: The answer's headers, lower-cased names.
    headers: Dict[str, str]
    #: The error object as the API sent it (a dict); the answer's parsed body
    #: when it had none; for a successful answer that was not JSON, its first
    #: 500 characters. None when there was nothing.
    error: Any

    def __init__(
        self,
        message: str = "",
        *,
        type: Optional[str] = None,
        param: Optional[str] = None,
        http_status: Optional[int] = None,
        headers: Optional[Mapping[str, str]] = None,
        error: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.type = type if type is not None else self._default_type
        self.param = param
        self.http_status = http_status
        self.headers = dict(headers or {})
        self.error = error

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r}, type={self.type!r}, http_status={self.http_status!r})"


class InvalidRequestError(OrboniError):
    """400, 404, 413, 422: a bad parameter (``param`` names it), no such object, or a body too large."""

    _default_type = "invalid_request_error"


class IdempotencyError(OrboniError):
    """400: an Idempotency-Key reused with other parameters; 409: its first request is still running."""

    _default_type = "idempotency_error"


class AuthenticationError(OrboniError):
    """401: no key, a wrong one or a revoked one."""

    _default_type = "authentication_error"


class PermissionError(OrboniError):  # noqa: A001 - orboni.PermissionError, never star-exported
    """403: a suspended or closed account, or a live key before the account's email is confirmed.

    This is ``orboni.PermissionError``, not Python's built-in one; ``from
    orboni import *`` leaves it out so it never hides the built-in.
    """

    _default_type = "permission_error"


class ReceivingFrozenError(OrboniError):
    """403: the owner froze receiving; live keys only read until it is unfrozen."""

    _default_type = "receiving_frozen"


class AccountPastDueError(OrboniError):
    """402: live only, an Orboni invoice is past due."""

    _default_type = "account_past_due"


class AmountTooSmallError(OrboniError):
    """422: under the minimum of every network the session could be paid on."""

    _default_type = "amount_too_small"


class RateLimitError(OrboniError):
    """429: too many requests or open sessions. ``retry_after`` says how long to wait."""

    _default_type = "rate_limit_error"

    @property
    def retry_after(self) -> Optional[int]:
        """Seconds to wait, from the Retry-After header; None without one."""
        try:
            return int(self.headers.get("retry-after", "").strip())
        except ValueError:
            return None


class RateUnavailableError(OrboniError):
    """503: no current exchange rate for the price's currency. Try again shortly."""

    _default_type = "rate_unavailable"


class APIError(OrboniError):
    """500, 503: Orboni's side, or an update in progress. Safe to retry with the same idempotency key."""

    _default_type = "api_error"


class APIConnectionError(OrboniError):
    """No answer: the network failed or the request timed out (``type`` ``connection_error``)."""

    _default_type = "connection_error"


class SignatureVerificationError(OrboniError):
    """A webhook whose signature, timestamp or body could not be verified. Answer it with 400."""

    _default_type = "signature_verification_error"


_BY_TYPE: Dict[str, Type[OrboniError]] = {
    "invalid_request_error": InvalidRequestError,
    "idempotency_error": IdempotencyError,
    "authentication_error": AuthenticationError,
    "permission_error": PermissionError,
    "receiving_frozen": ReceivingFrozenError,
    "account_past_due": AccountPastDueError,
    "amount_too_small": AmountTooSmallError,
    "rate_limit_error": RateLimitError,
    "rate_unavailable": RateUnavailableError,
    "api_error": APIError,
}


def error_from_response(http_status: int, headers: Mapping[str, str], body: Any) -> OrboniError:
    """The error for an answer that is not a success; ``body`` is its parsed JSON, or None."""
    error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
    found = error.get("type") if error is not None else None
    if isinstance(found, str):
        kind = found
    elif http_status >= 500:
        kind = "api_error"
    elif http_status == 401:
        kind = "authentication_error"
    else:
        kind = "invalid_request_error"
    message = error.get("message") if error is not None else None
    param = error.get("param") if error is not None else None
    cls = _BY_TYPE.get(kind, OrboniError)

    return cls(
        message if isinstance(message, str) else f"Orboni answered HTTP {http_status}.",
        type=kind,
        param=param if isinstance(param, str) else None,
        http_status=http_status,
        headers=headers,
        error=error if error is not None else body,
    )
