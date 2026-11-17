"""The client against a local stand-in for the API: what it sends, how it
retries, how it pages, and which error each answer becomes."""

from __future__ import annotations

import builtins
import copy
import json
import os
import pickle
import re
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Union

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import orboni  # noqa: E402
from orboni import (  # noqa: E402
    AccountPastDueError,
    AmountTooSmallError,
    APIConnectionError,
    APIError,
    AuthenticationError,
    IdempotencyError,
    InvalidRequestError,
    ListObject,
    OrboniError,
    OrboniObject,
    RateLimitError,
    RateUnavailableError,
    ReceivingFrozenError,
)

Answer = Union[Dict[str, Any], Callable[[BaseHTTPRequestHandler], None]]

#: Each request the stand-in received: method, url, headers (lower-cased), body.
seen: List[Dict[str, Any]] = []
#: What it answers, in order; the last one repeats.
answers: List[Answer] = []
_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        with _lock:
            seen.append(
                {
                    "method": self.command,
                    "url": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(answer):
            answer(self)
            return
        payload = answer.get("body", {})
        data = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
        self.send_response(answer.get("status", 200))
        self.send_header("Content-Type", "application/json")
        for name, value in answer.get("headers", {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # a dropped or abandoned connection, on purpose


server: Optional[_Server] = None
base = ""


def setUpModule() -> None:
    global server, base
    server = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"


def tearDownModule() -> None:
    if server is not None:
        server.shutdown()
        server.server_close()


def client(**config: Any) -> orboni.Orboni:
    return orboni.Orboni("sk_test_abc123", base_url=base, **config)


def session(id: str = "cs_test_1") -> Dict[str, Any]:
    return {"id": id, "object": "checkout.session", "status": "open"}


def drop(handler: BaseHTTPRequestHandler) -> None:
    """Closes the connection without an answer."""
    handler.close_connection = True
    handler.connection.shutdown(socket.SHUT_RDWR)


def slow(handler: BaseHTTPRequestHandler) -> None:
    """Answers after half a second."""
    time.sleep(0.5)
    handler.send_response(200)
    handler.end_headers()
    handler.wfile.write(b"{}")


class StandInTest(unittest.TestCase):
    def setUp(self) -> None:
        with _lock:
            seen.clear()
            answers.clear()

    def answer(self, *given: Answer) -> None:
        answers.extend(given)


class TheKey(unittest.TestCase):
    def test_is_required(self) -> None:
        for key in (None, "", "  "):
            with self.assertRaises(ValueError):
                orboni.Orboni(key)

    def test_is_never_a_publishable_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "publishable"):
            orboni.Orboni("pk_test_abc")

    def test_tells_test_mode(self) -> None:
        self.assertIs(orboni.Orboni("sk_test_x").test_mode, True)
        self.assertIs(orboni.Orboni("sk_live_x").test_mode, False)

    def test_is_not_shown(self) -> None:
        self.assertNotIn("sk_test_secret", repr(orboni.Orboni("sk_test_secret")))


class ARequest(StandInTest):
    def test_sends_the_key_json_and_who_is_asking(self) -> None:
        self.answer({"body": session()})
        created = client().checkout.sessions.create(amount=2500, currency="usd", metadata={"order_id": "1042"})

        self.assertEqual(created.id, "cs_test_1")
        request = seen[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "/v1/checkout/sessions")
        self.assertEqual(request["headers"]["authorization"], "Bearer sk_test_abc123")
        self.assertEqual(request["headers"]["accept"], "application/json")
        self.assertEqual(request["headers"]["content-type"], "application/json")
        self.assertRegex(request["headers"]["user-agent"], r"^orboni-python/\d+\.\d+\.\d+ python/\d+\.\d+")
        self.assertEqual(json.loads(request["body"]), {"amount": 2500, "currency": "usd", "metadata": {"order_id": "1042"}})

    def test_gives_every_post_an_idempotency_key_the_callers_when_given(self) -> None:
        self.answer({"body": session()})
        client().checkout.sessions.create(amount=100)
        client().checkout.sessions.create(amount=100, idempotency_key="order_1042")

        self.assertRegex(seen[0]["headers"]["idempotency-key"], r"^orboni-python-[0-9a-f-]{36}$")
        self.assertEqual(seen[1]["headers"]["idempotency-key"], "order_1042")

    def test_keeps_the_options_out_of_the_body(self) -> None:
        self.answer({"body": session()})
        client().checkout.sessions.create(amount=100, idempotency_key="k", timeout=5, max_retries=0)

        self.assertEqual(json.loads(seen[0]["body"]), {"amount": 100})

    def test_sends_no_idempotency_key_or_body_with_a_get_and_its_query_in_brackets(self) -> None:
        self.answer({"body": {"object": "list", "data": [], "has_more": False, "url": "/v1/checkout/sessions"}})
        client().checkout.sessions.list(
            limit=3,
            status="open",
            created={"gte": 1767225600, "lt": 1767312000},
            customer_email="a+b@example.com",
            ending_before=None,
        )

        request = seen[0]
        self.assertEqual(request["method"], "GET")
        self.assertNotIn("idempotency-key", request["headers"])
        self.assertNotIn("content-type", request["headers"])
        self.assertEqual(request["body"], "")
        self.assertEqual(
            request["url"],
            "/v1/checkout/sessions?limit=3&status=open&created%5Bgte%5D=1767225600&created%5Blt%5D=1767312000"
            "&customer_email=a%2Bb%40example.com",
        )

    def test_sends_booleans_as_words_and_lists_with_brackets(self) -> None:
        self.answer({"body": {"object": "list", "data": [], "has_more": False}})
        client().events.list(type=["checkout.session.completed", "refund.created"], created={})
        client().payment_links.list(active=False)

        self.assertEqual(seen[0]["url"], "/v1/events?type%5B%5D=checkout.session.completed&type%5B%5D=refund.created")
        self.assertEqual(seen[1]["url"], "/v1/payment_links?active=false")

    def test_escapes_an_id_and_refuses_an_empty_one(self) -> None:
        self.answer({"body": session("a/b")})
        client().checkout.sessions.retrieve("a/b")
        self.assertEqual(seen[0]["url"], "/v1/checkout/sessions/a%2Fb")

        with self.assertRaises(ValueError):
            client().checkout.sessions.retrieve("")
        with self.assertRaises(ValueError):
            client().refunds.void("  ")
        with self.assertRaises(TypeError):
            client().events.retrieve(None)  # type: ignore[arg-type]
        self.assertEqual(len(seen), 1, "nothing sent for an empty ID")

    def test_reaches_each_operation_at_its_path(self) -> None:
        self.answer({"body": {"id": "x", "object": "list", "data": [], "has_more": False}})
        api = client()
        api.checkout.sessions.expire("cs_test_1")
        api.payment_links.create(amount=500)
        api.payment_links.update("plink_1", active=False)
        api.payment_links.retrieve("plink_1")
        api.payment_links.list(active=True)
        api.refunds.create(checkout_session="cs_test_1")
        api.refunds.retrieve("re_1")
        api.refunds.list(checkout_session="cs_test_1")
        api.refunds.void("re_1")
        api.events.retrieve("evt_1")
        api.events.list(type="checkout.session.completed")
        api.events.resend("evt_1")
        api.payment_methods.list(amount=2500, currency="eur")
        api.currencies.list()
        api.checkout.sessions.create(amount=100)
        api.checkout.sessions.retrieve("cs_test_1")
        api.checkout.sessions.list()

        self.assertEqual(
            [f"{r['method']} {r['url']}" for r in seen],
            [
                "POST /v1/checkout/sessions/cs_test_1/expire",
                "POST /v1/payment_links",
                "POST /v1/payment_links/plink_1",
                "GET /v1/payment_links/plink_1",
                "GET /v1/payment_links?active=true",
                "POST /v1/refunds",
                "GET /v1/refunds/re_1",
                "GET /v1/refunds?checkout_session=cs_test_1",
                "POST /v1/refunds/re_1/void",
                "GET /v1/events/evt_1",
                "GET /v1/events?type=checkout.session.completed",
                "POST /v1/events/evt_1/resend",
                "GET /v1/payment_methods?amount=2500&currency=eur",
                "GET /v1/currencies",
                "POST /v1/checkout/sessions",
                "GET /v1/checkout/sessions/cs_test_1",
                "GET /v1/checkout/sessions",
            ],
        )
        for action in (0, 8, 11):
            self.assertEqual(seen[action]["body"], "", "expire, void and resend send no body")
            self.assertNotIn("content-type", seen[action]["headers"])
            self.assertEqual(seen[action]["headers"]["content-length"], "0")
            self.assertIn("idempotency-key", seen[action]["headers"])
        self.assertEqual(json.loads(seen[2]["body"]), {"active": False})

    def test_never_follows_a_redirect_with_the_key(self) -> None:
        self.answer({"status": 302, "headers": {"Location": f"{base}/currencies"}, "body": ""})
        with self.assertRaises(InvalidRequestError) as caught:
            client().currencies.list()
        self.assertEqual(caught.exception.http_status, 302)
        self.assertEqual(len(seen), 1)

    def test_keeps_the_builtin_permission_error_out_of_a_star_import(self) -> None:
        names: Dict[str, Any] = {}
        exec("from orboni import *", names)
        self.assertNotIn("PermissionError", names)
        self.assertIn("OrboniError", names)
        self.assertIsNot(orboni.PermissionError, builtins.PermissionError)
        self.assertTrue(issubclass(orboni.PermissionError, OrboniError))
        self.assertEqual(orboni.__version__, "1.0.0")


class AnError(StandInTest):
    cases = [
        (400, "invalid_request_error", InvalidRequestError),
        (404, "invalid_request_error", InvalidRequestError),
        (400, "idempotency_error", IdempotencyError),
        (401, "authentication_error", AuthenticationError),
        (403, "permission_error", orboni.PermissionError),
        (403, "receiving_frozen", ReceivingFrozenError),
        (402, "account_past_due", AccountPastDueError),
        (422, "amount_too_small", AmountTooSmallError),
    ]

    def test_is_the_class_of_its_type_and_not_retried(self) -> None:
        for status, kind, cls in self.cases:
            with self.subTest(status=status, type=kind):
                self.setUp()
                self.answer({"status": status, "body": {"error": {"type": kind, "message": "No.", "param": "amount"}}})
                with self.assertRaises(cls) as caught:
                    client().checkout.sessions.create(amount=1)

                error = caught.exception
                self.assertIs(type(error), cls)
                self.assertIsInstance(error, OrboniError)
                self.assertEqual(error.type, kind)
                self.assertEqual(error.message, "No.")
                self.assertEqual(str(error), "No.")
                self.assertEqual(error.param, "amount")
                self.assertEqual(error.http_status, status)
                self.assertEqual(error.error, {"type": kind, "message": "No.", "param": "amount"})
                self.assertEqual(error.headers["content-type"], "application/json")
                self.assertEqual(len(seen), 1, "not retried")

    def test_of_a_retried_type_is_its_class_once_the_retries_are_spent(self) -> None:
        for status, kind, cls in [
            (429, "rate_limit_error", RateLimitError),
            (503, "rate_unavailable", RateUnavailableError),
            (500, "api_error", APIError),
        ]:
            with self.subTest(type=kind):
                self.setUp()
                self.answer({"status": status, "body": {"error": {"type": kind, "message": "Later."}}})
                with self.assertRaises(cls) as caught:
                    client(max_retries=0).currencies.list()
                self.assertEqual(caught.exception.type, kind)
                self.assertIsNone(caught.exception.param)

    def test_of_a_type_the_library_does_not_know_is_still_an_orboni_error(self) -> None:
        self.answer({"status": 403, "body": {"error": {"type": "something_new", "message": "New."}}})
        with self.assertRaises(OrboniError) as caught:
            client().currencies.list()
        self.assertIs(type(caught.exception), OrboniError)
        self.assertEqual(caught.exception.type, "something_new")

    def test_without_a_json_body_still_says_the_status(self) -> None:
        self.answer({"status": 502, "body": "<html>Bad gateway</html>"})
        with self.assertRaises(APIError) as caught:
            client(max_retries=0).currencies.list()
        self.assertEqual(caught.exception.http_status, 502)
        self.assertEqual(caught.exception.type, "api_error")
        self.assertIn("502", caught.exception.message)

    def test_without_a_json_body_is_named_by_its_status(self) -> None:
        self.answer({"status": 401, "body": "Unauthorized"}, {"status": 413, "body": "Too large"})
        with self.assertRaises(AuthenticationError):
            client().currencies.list()
        with self.assertRaises(InvalidRequestError):
            client().currencies.list()

    def test_in_a_successful_answer_that_is_not_json_is_an_api_error(self) -> None:
        self.answer({"status": 200, "body": "<html>Hello</html>"})
        with self.assertRaises(APIError) as caught:
            client().currencies.list()
        self.assertEqual(caught.exception.type, "api_error")
        self.assertEqual(caught.exception.error, "<html>Hello</html>")
        self.assertEqual(len(seen), 1)

    def test_survives_a_copy_and_a_pickle(self) -> None:
        self.answer({"status": 400, "body": {"error": {"type": "invalid_request_error", "message": "No.", "param": "amount"}}})
        with self.assertRaises(InvalidRequestError) as caught:
            client().checkout.sessions.create(amount=1)
        for again in (copy.copy(caught.exception), pickle.loads(pickle.dumps(caught.exception))):
            self.assertIs(type(again), InvalidRequestError)
            self.assertEqual((again.type, again.message, again.param, again.http_status), ("invalid_request_error", "No.", "amount", 400))


class Retries(StandInTest):
    def test_try_again_after_a_5xx_with_the_same_idempotency_key(self) -> None:
        self.answer({"status": 503, "body": {"error": {"type": "api_error", "message": "Busy."}}}, {"body": session()})
        created = client().checkout.sessions.create(amount=100)

        self.assertEqual(created.id, "cs_test_1")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["headers"]["idempotency-key"], seen[1]["headers"]["idempotency-key"])
        self.assertEqual(seen[0]["body"], seen[1]["body"])

    def test_try_again_after_a_409_the_first_request_still_running(self) -> None:
        self.answer({"status": 409, "body": {"error": {"type": "idempotency_error", "message": "Running."}}}, {"body": session()})
        client().checkout.sessions.create(amount=100, idempotency_key="k")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["headers"]["idempotency-key"], "k")

    def test_wait_retry_after_on_a_429(self) -> None:
        self.answer(
            {"status": 429, "headers": {"Retry-After": "1"}, "body": {"error": {"type": "rate_limit_error", "message": "Slow down."}}},
            {"body": session()},
        )
        started = time.monotonic()
        client().checkout.sessions.retrieve("cs_test_1")
        self.assertGreaterEqual(time.monotonic() - started, 0.95)
        self.assertEqual(len(seen), 2)

    def test_do_not_wait_out_a_long_retry_after(self) -> None:
        self.answer({"status": 429, "headers": {"Retry-After": "120"}, "body": {"error": {"type": "rate_limit_error", "message": "Slow down."}}})
        started = time.monotonic()
        with self.assertRaises(RateLimitError) as caught:
            client().checkout.sessions.retrieve("cs_test_1")
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertEqual(len(seen), 1)
        self.assertLess(time.monotonic() - started, 1)

    def test_stop_after_max_retries(self) -> None:
        self.answer({"status": 500, "body": {"error": {"type": "api_error", "message": "Ours."}}})
        with self.assertRaises(APIError):
            client(max_retries=1).currencies.list()
        self.assertEqual(len(seen), 2)

    def test_follow_the_calls_own_max_retries(self) -> None:
        self.answer({"status": 500, "body": {"error": {"type": "api_error", "message": "Ours."}}})
        with self.assertRaises(APIError):
            client(max_retries=2).currencies.list(max_retries=0)
        self.assertEqual(len(seen), 1)

    def test_never_follow_another_4xx(self) -> None:
        self.answer({"status": 400, "body": {"error": {"type": "invalid_request_error", "message": "No."}}})
        with self.assertRaises(InvalidRequestError):
            client(max_retries=2).checkout.sessions.create(amount=1)
        self.assertEqual(len(seen), 1)

    def test_try_again_when_the_connection_drops_then_say_so(self) -> None:
        self.answer(drop)
        with self.assertRaises(APIConnectionError) as caught:
            client(max_retries=2).currencies.list()
        self.assertEqual(caught.exception.type, "connection_error")
        self.assertIsNone(caught.exception.http_status)
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertEqual(len(seen), 3)

    def test_end_a_call_that_takes_too_long(self) -> None:
        self.answer(slow)
        with self.assertRaises(APIConnectionError) as caught:
            client(timeout=0.1, max_retries=0).currencies.list()
        self.assertRegex(caught.exception.message, r"0\.1 s")

    def test_end_a_call_at_its_own_timeout(self) -> None:
        self.answer(slow)
        started = time.monotonic()
        with self.assertRaises(APIConnectionError):
            client(max_retries=0).currencies.list(timeout=0.1)
        self.assertLess(time.monotonic() - started, 0.45)

    def test_say_so_when_nothing_listens(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        api = orboni.Orboni("sk_test_x", base_url=f"http://127.0.0.1:{port}/v1", max_retries=0)
        with self.assertRaisesRegex(APIConnectionError, "Could not reach Orboni"):
            api.currencies.list()


class AList(StandInTest):
    @staticmethod
    def page(ids: List[str], has_more: bool) -> Dict[str, Any]:
        return {"body": {"object": "list", "data": [{"id": id} for id in ids], "has_more": has_more, "url": "/v1/refunds"}}

    def test_called_is_one_page(self) -> None:
        self.answer(self.page(["re_3", "re_2"], True))
        page = client().refunds.list(limit=2)
        self.assertIsInstance(page, ListObject)
        self.assertEqual([r.id for r in page.data], ["re_3", "re_2"])
        self.assertIs(page.has_more, True)
        self.assertEqual(len(seen), 1)

    def test_iterated_is_every_object_asked_page_by_page(self) -> None:
        self.answer(self.page(["re_5", "re_4"], True), self.page(["re_3", "re_2"], True), self.page(["re_1"], False))
        ids = [refund.id for refund in client().refunds.list(limit=2, checkout_session="cs_test_1").auto_paging_iter()]
        self.assertEqual(ids, ["re_5", "re_4", "re_3", "re_2", "re_1"])
        self.assertEqual(
            [r["url"] for r in seen],
            [
                "/v1/refunds?limit=2&checkout_session=cs_test_1",
                "/v1/refunds?limit=2&checkout_session=cs_test_1&starting_after=re_4",
                "/v1/refunds?limit=2&checkout_session=cs_test_1&starting_after=re_2",
            ],
        )

    def test_iterated_from_ending_before_goes_the_other_way(self) -> None:
        self.answer(self.page(["re_7", "re_6"], True), self.page(["re_8"], False))
        ids = [refund.id for refund in client().refunds.list(limit=2, ending_before="re_5").auto_paging_iter()]
        self.assertEqual(ids, ["re_6", "re_7", "re_8"])
        self.assertEqual(seen[1]["url"], "/v1/refunds?limit=2&ending_before=re_7")

    def test_iterated_asks_lazily_and_with_the_calls_options(self) -> None:
        self.answer(self.page(["re_5", "re_4"], True), {"status": 500, "body": {"error": {"type": "api_error", "message": "Ours."}}})
        walk = client(max_retries=2).refunds.list(limit=2, max_retries=0).auto_paging_iter()
        self.assertEqual(next(walk).id, "re_5")
        self.assertEqual(next(walk).id, "re_4")
        self.assertEqual(len(seen), 1)
        with self.assertRaises(APIError):
            next(walk)
        self.assertEqual(len(seen), 2, "the call's max_retries=0 holds for the next pages too")

    def test_stops_early_when_asked_for_a_number(self) -> None:
        self.answer(self.page(["re_5", "re_4"], True), self.page(["re_3", "re_2"], True))
        items = client().refunds.list(limit=2).auto_paging_to_list(limit=3)
        self.assertEqual([r.id for r in items], ["re_5", "re_4", "re_3"])
        self.assertEqual(len(seen), 2)

    def test_needs_a_number_from_1_to_10000(self) -> None:
        self.answer(self.page(["re_1"], False))
        page = client().refunds.list()
        for limit in (0, 10_001):
            with self.assertRaises(ValueError):
                page.auto_paging_to_list(limit=limit)
        with self.assertRaises(TypeError):
            page.auto_paging_to_list(limit="5")  # type: ignore[arg-type]
        self.assertEqual(len(page.auto_paging_to_list(limit=10_000)), 1)

    def test_never_paged_is_its_one_page(self) -> None:
        self.answer({"body": {"object": "list", "data": [{"id": "usd", "code": "usd"}], "has_more": True, "url": "/v1/currencies"}})
        currencies = client().currencies.list()
        self.assertIsInstance(currencies, ListObject)
        self.assertEqual([c.code for c in currencies.auto_paging_iter()], ["usd"])
        self.assertEqual(len(seen), 1)


class AnAnswer(StandInTest):
    def test_reads_as_a_dict_and_as_attributes_all_the_way_down(self) -> None:
        self.answer(
            {
                "body": {
                    "id": "cs_test_1",
                    "object": "checkout.session",
                    "url": "https://orboni.com/pay/cs_test_1",
                    "metadata": {"order_id": "1042"},
                    "transactions": [{"hash": "0xabc", "from": "0xdef"}],
                    "payment": None,
                }
            }
        )
        found = client().checkout.sessions.retrieve("cs_test_1")

        self.assertIsInstance(found, OrboniObject)
        self.assertIsInstance(found, dict)
        self.assertEqual(found["url"], found.url)
        self.assertIsInstance(found.metadata, OrboniObject)
        self.assertEqual(found.metadata.order_id, "1042")
        self.assertIsInstance(found.transactions, list)
        self.assertIsInstance(found.transactions[0], OrboniObject)
        self.assertEqual(found.transactions[0]["from"], "0xdef")
        self.assertIsNone(found.payment)
        self.assertIn("metadata", dir(found))
        self.assertEqual(json.loads(json.dumps(found))["metadata"], {"order_id": "1042"})

    def test_says_what_is_missing(self) -> None:
        found = OrboniObject({"id": "x"})
        with self.assertRaises(AttributeError):
            found.status
        with self.assertRaises(KeyError):
            found["status"]
        self.assertFalse(hasattr(found, "status"))
        self.assertEqual(getattr(found, "status", "none"), "none")

    def test_writes_through_to_the_dict(self) -> None:
        found = OrboniObject(id="x", nested={"a": 1})
        found.status = "open"
        self.assertEqual(found["status"], "open")
        del found.status
        self.assertNotIn("status", found)
        self.assertIsInstance(found.nested, OrboniObject)

    def test_survives_a_copy_and_a_pickle(self) -> None:
        found = OrboniObject({"id": "x", "metadata": {"a": "1"}})
        page = ListObject({"object": "list", "data": [{"id": "re_1"}], "has_more": False})
        for again in (copy.deepcopy(found), pickle.loads(pickle.dumps(found))):
            self.assertEqual(again, found)
            self.assertEqual(again.metadata.a, "1")
        again_page = pickle.loads(pickle.dumps(page))
        self.assertEqual([r.id for r in again_page.auto_paging_iter()], ["re_1"])


if __name__ == "__main__":
    unittest.main()
