from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any, Optional

from ._object import ListObject, OrboniObject

if TYPE_CHECKING:
    from ._client import Orboni

#: What encodeURIComponent leaves as it is, so an ID is escaped as in the other libraries.
_SAFE = "-_.!~*'()"


class _Resource:
    """The part every resource shares: its client and a checked path.

    Every call takes three keyword-only options, never sent as parameters:
    ``idempotency_key`` (sent with a POST only; one is made when not given),
    ``timeout`` (seconds) and ``max_retries``, each defaulting to the client's.
    """

    def __init__(self, client: Orboni) -> None:
        self._client = client

    @staticmethod
    def _path(base: str, id: str, action: str = "") -> str:
        """``/refunds/re_…``: the ID checked and escaped, so an empty one never reaches the list."""
        if not isinstance(id, str):
            raise TypeError(f"An ID (a str) is needed for {base}/{{id}}{action}.")
        if id.strip() == "":
            raise ValueError(f"An ID is needed for {base}/{{id}}{action}.")

        return f"{base}/{urllib.parse.quote(id, safe=_SAFE)}{action}"


class CheckoutSessions(_Resource):
    """``/v1/checkout/sessions``: one payment you wait for, and its payment page."""

    def create(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> OrboniObject:
        """Makes a session; send the buyer to its ``url``."""
        return self._client._request(
            "POST", "/checkout/sessions", params, idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def retrieve(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        """A session as it is now: read it back before you fulfil an order."""
        return self._client._request(
            "GET", self._path("/checkout/sessions", id), idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def list(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> ListObject:
        """Sessions, newest first: ``limit``, ``starting_after``, ``ending_before``, ``created``,
        ``status``, ``payment_status``, ``client_reference_id``, ``customer_email``."""
        return self._client._list("/checkout/sessions", params, timeout=timeout, max_retries=max_retries)

    def expire(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        """Closes an open session now. Money sent to it later is held for your review."""
        return self._client._request(
            "POST",
            self._path("/checkout/sessions", id, "/expire"),
            idempotency_key=idempotency_key,
            timeout=timeout,
            max_retries=max_retries,
        )


class Checkout:
    """``client.checkout.sessions``."""

    def __init__(self, client: Orboni) -> None:
        self.sessions = CheckoutSessions(client)


class PaymentLinks(_Resource):
    """``/v1/payment_links``: one address that makes a session for each buyer who opens it."""

    def create(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> OrboniObject:
        return self._client._request(
            "POST", "/payment_links", params, idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def retrieve(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        return self._client._request(
            "GET", self._path("/payment_links", id), idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def update(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> OrboniObject:
        """Changes the fields given; ``active=False`` stops it taking payments."""
        return self._client._request(
            "POST",
            self._path("/payment_links", id),
            params,
            idempotency_key=idempotency_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    def list(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> ListObject:
        """Payment links; ``active`` keeps only the links that take payments (True) or are switched off (False)."""
        return self._client._list("/payment_links", params, timeout=timeout, max_retries=max_retries)


class Refunds(_Resource):
    """``/v1/refunds``: a record of money you sent back from your own wallet.

    Orboni holds no money, so recording a refund sends nothing.
    """

    def create(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> OrboniObject:
        return self._client._request(
            "POST", "/refunds", params, idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def retrieve(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        return self._client._request(
            "GET", self._path("/refunds", id), idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def list(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> ListObject:
        """Refunds; ``checkout_session`` keeps only the refunds of that session."""
        return self._client._list("/refunds", params, timeout=timeout, max_retries=max_retries)

    def void(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        """Takes back a refund recorded by mistake."""
        return self._client._request(
            "POST",
            self._path("/refunds", id, "/void"),
            idempotency_key=idempotency_key,
            timeout=timeout,
            max_retries=max_retries,
        )


class Events(_Resource):
    """``/v1/events``: what your webhooks were sent, kept so you can read them again."""

    def retrieve(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        return self._client._request(
            "GET", self._path("/events", id), idempotency_key=idempotency_key, timeout=timeout, max_retries=max_retries
        )

    def list(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> ListObject:
        """Events, newest first; ``type`` and ``checkout_session`` narrow them."""
        return self._client._list("/events", params, timeout=timeout, max_retries=max_retries)

    def resend(
        self,
        id: str,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        """Sends the event to your endpoints again."""
        return self._client._request(
            "POST",
            self._path("/events", id, "/resend"),
            idempotency_key=idempotency_key,
            timeout=timeout,
            max_retries=max_retries,
        )


class PaymentMethods(_Resource):
    """``/v1/payment_methods``: the coins and networks your project can be paid in now."""

    def list(
        self,
        *,
        amount: Optional[int] = None,
        currency: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **params: Any,
    ) -> ListObject:
        """One page, never paged. ``amount`` (in ``currency``'s smallest unit) leaves out the
        methods whose minimum is above it."""
        query = {"amount": amount, "currency": currency, **params}
        return self._client._list("/payment_methods", query, paged=False, timeout=timeout, max_retries=max_retries)


class Currencies(_Resource):
    """``/v1/currencies``: the currencies a price can be in, and their decimals."""

    def list(
        self,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> ListObject:
        """One page, never paged."""
        return self._client._list("/currencies", {}, paged=False, timeout=timeout, max_retries=max_retries)
