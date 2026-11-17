from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, List, Optional, Sequence, Union

from ._errors import SignatureVerificationError
from ._object import OrboniObject, convert_to_object

Payload = Union[str, bytes, bytearray, memoryview]
Header = Union[str, Sequence[str], None]
Secret = Union[str, Sequence[str]]

_DIGITS = re.compile(r"[0-9]+")


class Webhook:
    """Orboni signs every webhook.

    ``Webhook-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw
    body>" with your endpoint's signing secret>``, one ``v1`` per secret while
    a rolled secret still signs. Verifying needs no API key::

        event = orboni.Webhook.construct_event(request.body, request.headers.get("Webhook-Signature"), secret)
    """

    #: The header Orboni signs with.
    HEADER = "Webhook-Signature"
    #: How far the signature's time may be from now, in seconds.
    DEFAULT_TOLERANCE = 300

    @staticmethod
    def construct_event(
        payload: Payload,
        header: Header,
        secret: Secret,
        tolerance: float = DEFAULT_TOLERANCE,
        *,
        now: Optional[float] = None,
    ) -> OrboniObject:
        """The event, once its signature is checked; raises SignatureVerificationError otherwise.

        ``payload`` is the request's body exactly as it arrived (bytes or a
        str), never JSON your framework parsed and wrote again. ``header`` is
        the Webhook-Signature header (a list: its first is read). ``secret``
        is the endpoint's ``whsec_…`` signing secret, or a list of them while
        one is rolled. ``now`` (Unix seconds) is for tests only.
        """
        body = _bytes(payload)
        Webhook.verify_signature(body, header, secret, tolerance, now=now)
        try:
            event = json.loads(body.decode("utf-8"))
        except ValueError as error:
            raise SignatureVerificationError("The webhook is signed but its body is not JSON.") from error

        return convert_to_object(event)  # type: ignore[no-any-return]

    @staticmethod
    def verify_signature(
        payload: Payload,
        header: Header,
        secret: Secret,
        tolerance: float = DEFAULT_TOLERANCE,
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Checks the signature only: True, or raises SignatureVerificationError when it does not hold."""
        body = _bytes(payload)
        value: Any = header
        if isinstance(header, (list, tuple)):
            value = header[0] if header else None
        if not isinstance(value, str) or value.strip() == "":
            raise SignatureVerificationError(f"No {Webhook.HEADER} header: this request did not come from Orboni.")

        timestamp: Optional[int] = None
        signatures: List[str] = []
        for pair in value.split(","):
            key, sign, part = pair.partition("=")
            key = key.strip()
            part = part.strip() if sign else ""
            if key == "t":
                if timestamp is None and _DIGITS.fullmatch(part):
                    timestamp = int(part)
            elif key == "v1":
                signatures.append(part)
        if timestamp is None or timestamp <= 0 or not signatures:
            raise SignatureVerificationError(f"The {Webhook.HEADER} header is not t=…,v1=….")

        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
            raise TypeError("The tolerance is a number of seconds above 0.")
        if not tolerance > 0:
            raise ValueError("The tolerance is a number of seconds above 0.")
        current = int(time.time()) if now is None else now
        distance = abs(current - timestamp)
        if distance > tolerance:
            raise SignatureVerificationError(
                f"The signature is {distance} s from now, more than the {tolerance} s allowed:"
                " an old webhook replayed, or this server's clock is wrong."
            )

        candidates = [secret] if isinstance(secret, str) else list(secret or [])
        secrets = [s for s in candidates if isinstance(s, str) and s != ""]
        if not secrets:
            raise ValueError("A webhook needs your endpoint's signing secret (whsec_…) to be verified.")
        signed = str(timestamp).encode("ascii") + b"." + body
        given = [candidate.encode("utf-8", "replace") for candidate in signatures]
        for key in secrets:
            expected = hmac.new(key.encode("utf-8"), signed, hashlib.sha256).hexdigest().encode("ascii")
            if any(hmac.compare_digest(expected, candidate) for candidate in given):
                return True

        raise SignatureVerificationError(
            "No signature matches: check that the signing secret is this endpoint’s, of this mode (test or live),"
            " and that the body was not changed."
        )


def _bytes(payload: Any) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8", "replace")
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    raise SignatureVerificationError(
        "Pass the raw request body (bytes or a str), not parsed JSON: the signature is over the bytes as sent."
    )
