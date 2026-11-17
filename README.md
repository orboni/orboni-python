# Orboni for Python

The official Python library for the [Orboni API](https://orboni.com/docs): take crypto payments — USDT, USDC, BTC, ETH and more — paid straight to your own wallets. Orboni never holds your money.

- Every API call: checkout sessions, payment links, refunds, events, payment methods and currencies.
- Webhook signatures verified with the standard library's `hmac`, in constant time.
- Safe retries: each POST carries an idempotency key, and network failures, timeouts, `409`, `429` and `5xx` are tried again.
- No dependencies: the standard library only. Python 3.9 or later, with type hints (`py.typed`).

## Install

```bash
pip install orboni
```

## Take a payment

Make a checkout session on your server and send the buyer to its `url`. Amounts are in the currency's smallest unit (cents, or whole yen).

```python
import os

import orboni

client = orboni.Orboni(os.environ["ORBONI_SECRET_KEY"])  # sk_test_… or sk_live_…

session = client.checkout.sessions.create(
    amount=2500,
    currency="usd",
    client_reference_id="order_1042",
    success_url="https://your-store.example/orders/1042",
    cancel_url="https://your-store.example/cart",
    metadata={"order_id": "1042"},
    idempotency_key="order_1042",
)

# redirect the buyer to session.url
```

The buyer picks a coin on Orboni's payment page and pays from any wallet or exchange. Fulfil the order when the `checkout.session.completed` webhook arrives, or read the session back:

```python
paid = client.checkout.sessions.retrieve(session.id)
if paid.status == "complete" and paid.payment_status == "paid":
    ...  # fulfil order 1042
```

The API's parameters are keyword arguments with the API's own names. `idempotency_key`, `timeout` and `max_retries` are the library's options for that call and are never sent as parameters.

## Answers

Every answer is an `orboni.OrboniObject`: a `dict` whose fields also read as attributes, nested objects included. `session["url"]` and `session.url` are the same; a missing field raises `KeyError` with brackets and `AttributeError` as an attribute. JSON arrays stay lists, and `json.dumps(session)` works as for any dict. A field named after a Python keyword reads with brackets only: a transaction's `transaction["from"]`.

## Webhooks

Orboni signs every webhook with your endpoint's signing secret (`whsec_…`, under Developers → Webhooks in the dashboard). Verify it against the **raw** body, before any JSON parser touches it.

Flask:

```python
import os

import orboni
from flask import Flask, request

app = Flask(__name__)

@app.post("/webhooks/orboni")
def orboni_webhook():
    try:
        event = orboni.Webhook.construct_event(
            request.get_data(),
            request.headers.get("Webhook-Signature"),
            os.environ["ORBONI_WEBHOOK_SECRET"],
        )
    except orboni.SignatureVerificationError as error:
        return str(error), 400

    if event.type == "checkout.session.completed":
        session = event.data.object  # fulfil session.client_reference_id once
    return "", 200
```

Django:

```python
import os

import orboni
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def orboni_webhook(request):
    try:
        event = orboni.Webhook.construct_event(
            request.body,
            request.headers.get("Webhook-Signature"),
            os.environ["ORBONI_WEBHOOK_SECRET"],
        )
    except orboni.SignatureVerificationError as error:
        return HttpResponseBadRequest(str(error))

    if event.type == "checkout.session.completed":
        session = event.data.object  # fulfil session.client_reference_id once
    return HttpResponse(status=200)
```

In FastAPI or Starlette, pass `await request.body()` and `request.headers.get("webhook-signature")`.

The same event can arrive more than once and out of order: keep the `id`s you have handled. In production, ignore events whose `livemode` is `False`. While you roll a signing secret, pass both: `construct_event(body, header, [new_secret, old_secret])`. Verifying needs no API key: `orboni.Webhook.construct_event(…)` is a static method, and `client.webhooks.construct_event(…)` is the same. `Webhook.verify_signature(…)` checks the signature only and returns `True`. The signature's time may be at most 300 seconds from now, either way; the fourth argument changes that.

## Lists

A list call answers one page, an `orboni.ListObject`. `auto_paging_iter()` walks every object across the pages, asking for each next page as it goes:

```python
page = client.checkout.sessions.list(limit=10, status="complete")
print(page.data, page.has_more)

for refund in client.refunds.list(checkout_session="cs_live_…").auto_paging_iter():
    print(refund.id, refund.amount)

recent = client.events.list(created={"gte": 1767225600}).auto_paging_to_list(limit=500)
```

A list asked with `ending_before` is walked backwards, each page's objects oldest first. Payment methods and currencies are one page, never paged.

## Everything else

```python
client.checkout.sessions.expire("cs_live_…")  # an order cancelled on your side

link = client.payment_links.create(amount=1500, currency="eur", description="Workshop ticket")
client.payment_links.update(link.id, active=False)

# Orboni holds no money: send the refund from your wallet, then record it
client.refunds.create(checkout_session="cs_live_…", amount=1000, reason="requested_by_customer", transaction="0x…")

client.events.resend("evt_…")
methods = client.payment_methods.list(amount=2500, currency="usd")
currencies = client.currencies.list()
```

The parameters and fields of each call are in the [API reference](https://orboni.com/docs).

## Errors

A refused call raises an `orboni.OrboniError` subclass named after the API's error `type`. Branch on the class or on `type`, never on the message:

| Class | `type` | When |
| --- | --- | --- |
| `InvalidRequestError` | `invalid_request_error` | A bad parameter (`param` names it), or no such object (`http_status` 404) |
| `AmountTooSmallError` | `amount_too_small` | Under the minimum of every network the session could be paid on |
| `IdempotencyError` | `idempotency_error` | An idempotency key reused with other parameters |
| `AuthenticationError` | `authentication_error` | No key, a wrong one or a revoked one |
| `PermissionError` | `permission_error` | A suspended or closed account, or a live key before the email is confirmed |
| `ReceivingFrozenError` | `receiving_frozen` | Receiving is frozen: live keys only read |
| `AccountPastDueError` | `account_past_due` | An Orboni invoice is past due (live only) |
| `RateLimitError` | `rate_limit_error` | Too many requests; `retry_after` says how many seconds to wait |
| `RateUnavailableError` | `rate_unavailable` | No current exchange rate for the currency |
| `APIError` | `api_error` | Orboni's side; safe to retry with the same idempotency key |
| `APIConnectionError` | `connection_error` | No answer: the network failed or the call timed out |

Each has `type`, `message`, `param`, `http_status`, `headers` (lower-cased names) and `error` (the error object as the API sent it). `PermissionError` is `orboni.PermissionError`, not Python's built-in one: refer to it through the package, as below. `from orboni import *` leaves it out so it never hides the built-in.

```python
import orboni

try:
    client.checkout.sessions.create(amount=50, currency="usd")
except orboni.AmountTooSmallError:
    ...  # offer another way to pay
except orboni.InvalidRequestError as error:
    print(error.param, error.message)
except orboni.PermissionError:
    ...  # the account cannot take payments now
```

## Configuration

```python
client = orboni.Orboni(
    os.environ["ORBONI_SECRET_KEY"],
    timeout=30.0,  # seconds a try may wait to connect, and then for each part of the answer
    max_retries=2,  # after the first try
)

# per call
client.checkout.sessions.retrieve("cs_live_…", timeout=5.0, max_retries=0)
```

Without `idempotency_key` the library makes one for each POST, so its own retries never create a second session or refund. Pass your own (an order ID is a good one) to make retries of your own safe too: Orboni keeps a successful answer for 24 hours and gives it again for the same key.

## Test mode

A test key (`sk_test_…`) makes test sessions that take no real money. Open a test session's payment page while signed in to the dashboard and press **Simulate payment** to see the whole flow, webhooks included. `client.test_mode` says which kind of key a client has. See [Testing](https://orboni.com/docs/testing).

## License

MIT
