"""Orboni's API for Python: crypto payments paid straight to your own wallets.

    import orboni

    client = orboni.Orboni(os.environ["ORBONI_SECRET_KEY"])
    session = client.checkout.sessions.create(amount=2500, currency="usd")

See https://orboni.com/docs/libraries.
"""

from ._client import Orboni
from ._errors import (
    AccountPastDueError,
    AmountTooSmallError,
    APIConnectionError,
    APIError,
    AuthenticationError,
    IdempotencyError,
    InvalidRequestError,
    OrboniError,
    RateLimitError,
    RateUnavailableError,
    ReceivingFrozenError,
    SignatureVerificationError,
)
from ._errors import PermissionError as PermissionError  # orboni.PermissionError; not in __all__
from ._object import ListObject, OrboniObject
from ._version import __version__ as __version__
from ._webhooks import Webhook

# PermissionError is left out on purpose: `from orboni import *` must never
# hide Python's built-in PermissionError. Use orboni.PermissionError.
__all__ = [
    "Orboni",
    "OrboniObject",
    "ListObject",
    "Webhook",
    "OrboniError",
    "InvalidRequestError",
    "IdempotencyError",
    "AuthenticationError",
    "ReceivingFrozenError",
    "AccountPastDueError",
    "AmountTooSmallError",
    "RateLimitError",
    "RateUnavailableError",
    "APIError",
    "APIConnectionError",
    "SignatureVerificationError",
]
