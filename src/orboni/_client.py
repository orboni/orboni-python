from __future__ import annotations

import http.client
import json
import math
import platform
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import Message
from typing import Any, Dict, List, Mapping, Optional, Tuple, cast

from ._errors import APIConnectionError, APIError, error_from_response
from ._object import ListObject, OrboniObject, convert_to_object
from ._resources import Checkout, Currencies, Events, PaymentLinks, PaymentMethods, Refunds
from ._version import __version__
from ._webhooks import Webhook

DEFAULT_BASE_URL = "https://orboni.com/v1"

#: A delay longer than this in Retry-After is not waited out: the error is raised.
MAX_RETRY_AFTER_SECONDS = 20

#: What encodeURIComponent leaves as it is, so a query is escaped as in the other libraries.
_SAFE = "-_.!~*'()"

_NOT_JSON = object()


class Orboni:
    """Orboni's API. One instance per secret key; it keeps no state between calls.

        import orboni
        client = orboni.Orboni(os.environ["ORBONI_SECRET_KEY"])
        session = client.checkout.sessions.create(amount=2500, currency="usd")
    """

    checkout: Checkout
    payment_links: PaymentLinks
    refunds: Refunds
    events: Events
    payment_methods: PaymentMethods
    currencies: Currencies
    #: Verifies the webhooks Orboni sends; needs no key.
    webhooks: Webhook

    def __init__(
        self,
        api_key: Optional[str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """``api_key`` is your secret key (``sk_test_…`` or ``sk_live_…``). ``timeout`` is in
        seconds, for each try; ``max_retries`` the tries after the first for a network failure,
        a timeout, a 409, 429 or 5xx."""
        if api_key is None or (isinstance(api_key, str) and api_key.strip() == ""):
            raise ValueError(
                "Orboni needs your secret key (sk_test_… or sk_live_…), from Developers → API keys in the dashboard."
            )
        if not isinstance(api_key, str):
            raise TypeError("The secret key is a str (sk_test_… or sk_live_…).")
        key = api_key.strip()
        if key.startswith("pk_"):
            raise ValueError(
                "That is a publishable key, made for the browser. The API takes your secret key (sk_test_… or sk_live_…)."
            )
        base = base_url.rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise ValueError("base_url is an http:// or https:// address, such as https://orboni.com/v1.")

        self._api_key = key
        self._base_url = base
        self._timeout = timeout
        self._max_retries = max_retries
        # Redirects are not followed: the key goes nowhere but base_url.
        self._opener = urllib.request.build_opener(_NoRedirects())
        self._user_agent = f"orboni-python/{__version__} python/{platform.python_version()}"

        self.checkout = Checkout(self)
        self.payment_links = PaymentLinks(self)
        self.refunds = Refunds(self)
        self.events = Events(self)
        self.payment_methods = PaymentMethods(self)
        self.currencies = Currencies(self)
        self.webhooks = Webhook()

    @property
    def test_mode(self) -> bool:
        """True for a test key: what it makes never takes real money."""
        return self._api_key.startswith("sk_test_")

    def __repr__(self) -> str:
        return f"<orboni.Orboni {'test' if self.test_mode else 'live'} mode, {self._base_url}>"

    def _list(
        self,
        path: str,
        params: Mapping[str, Any],
        *,
        paged: bool = True,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> ListObject:
        """One page of a list; a paged one also knows how to ask for the next."""

        def fetch(page_params: Dict[str, Any]) -> ListObject:
            answer = self._request("GET", path, page_params, timeout=timeout, max_retries=max_retries)
            page = answer if isinstance(answer, ListObject) else ListObject(answer)
            if paged:
                page._attach(fetch, page_params)
            return page

        return fetch(dict(params))

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> OrboniObject:
        """Any call of the API; the resources are its shortcuts."""
        url = self._base_url + path
        body: Optional[bytes] = None
        if method == "GET":
            query = encode_query(params or {})
            url += f"?{query}" if query else ""
        elif params is not None:
            body = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method == "POST":
            headers["Idempotency-Key"] = (
                idempotency_key if idempotency_key is not None else f"orboni-python-{uuid.uuid4()}"
            )

        retries = self._max_retries if max_retries is None else max_retries
        limit = self._timeout if timeout is None else timeout
        attempt = 0
        while True:
            try:
                status, answer_headers, raw = self._send(method, url, headers, body, limit)
            except (OSError, http.client.HTTPException) as failure:
                if attempt >= retries:
                    raise _connection_error(failure, limit) from failure
                time.sleep(_backoff(attempt))
                attempt += 1
                continue

            text = raw.decode("utf-8", "replace")
            data = _parse(text)
            if 200 <= status < 300:
                if data is _NOT_JSON:
                    raise APIError(
                        "Orboni answered with something that is not JSON.",
                        type="api_error",
                        http_status=status,
                        headers=answer_headers,
                        error=text[:500],
                    )
                return cast(OrboniObject, convert_to_object(data))

            error = error_from_response(status, answer_headers, None if data is _NOT_JSON else data)
            wait = _retry_delay(status, answer_headers, attempt) if attempt < retries else None
            if wait is None:
                raise error
            time.sleep(wait)
            attempt += 1

    def _send(
        self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float
    ) -> Tuple[int, Dict[str, str], bytes]:
        """One try: the status, the headers (lower-cased) and the body of whatever answered."""
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as answer:
            try:
                return answer.code, _lower(answer.headers), answer.read()
            finally:
                answer.close()
        with response:
            return response.status, _lower(response.headers), response.read()


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A 3xx is answered as it is, an error, never followed with the key to another address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _lower(headers: Message) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        out[key] = f"{out[key]}, {value}" if key in out else value
    return out


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return _NOT_JSON


def _connection_error(failure: BaseException, timeout: float) -> APIConnectionError:
    reason: Any = failure.reason if isinstance(failure, urllib.error.URLError) else failure
    if isinstance(reason, (socket.timeout, TimeoutError)):
        message = f"Orboni did not answer within {timeout:g} s."
    else:
        message = f"Could not reach Orboni: {str(reason) or type(reason).__name__}"
    return APIConnectionError(message, type="connection_error")


def _retry_delay(status: int, headers: Mapping[str, str], attempt: int) -> Optional[float]:
    """Seconds to wait before the next try, or None when this answer is not worth another."""
    if status != 409 and status != 429 and status < 500:
        return None
    value = headers.get("retry-after")
    if value is not None:
        try:
            after = float(value)
        except ValueError:
            after = math.nan
        if math.isfinite(after) and after >= 0:
            return None if after > MAX_RETRY_AFTER_SECONDS else after
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    """0.5 s, 1 s, 2 s… up to 8 s, less up to a quarter at random so clients that failed together do not retry together."""
    base = min(0.5 * 2.0**attempt, 8.0)
    return base * (0.75 + random.random() * 0.25)


def encode_query(params: Mapping[str, Any], prefix: str = "") -> str:
    """``{"limit": 10, "created": {"gte": 1}}`` → ``limit=10&created%5Bgte%5D=1``; None is left out."""
    parts: List[str] = []
    for key, value in params.items():
        if value is None:
            continue
        name = str(key) if prefix == "" else f"{prefix}[{key}]"
        if isinstance(value, Mapping):
            nested = encode_query(value, name)
            if nested:
                parts.append(nested)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if item is not None:
                    parts.append(f"{_quote(name + '[]')}={_quote(_scalar(item))}")
        else:
            parts.append(f"{_quote(name)}={_quote(_scalar(value))}")
    return "&".join(parts)


def _scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _quote(text: str) -> str:
    return urllib.parse.quote(text, safe=_SAFE)
