"""Webhook signatures, against cases made by Orboni's own server
(fixtures/webhooks.json): what the server signs, the library accepts, and
nothing else."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import orboni  # noqa: E402
from orboni import OrboniObject, SignatureVerificationError, Webhook  # noqa: E402

with open(os.path.join(HERE, "fixtures", "webhooks.json"), encoding="utf-8") as file:
    vectors: Dict[str, Any] = json.load(file)


def case(name: str) -> Dict[str, Any]:
    return next(c for c in vectors["cases"] if c["name"] == name)


def sign(secret: str, t: int, body: str) -> str:
    return hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()


class ASignatureMadeByOrboni(unittest.TestCase):
    def test_every_case_has_the_servers_answer(self) -> None:
        self.assertGreaterEqual(len(vectors["cases"]), 17)
        for c in vectors["cases"]:
            with self.subTest(c["name"]):
                if c["valid"]:
                    event = Webhook.construct_event(c["body"], c["header"], c["secret"], vectors["tolerance"], now=c["now"])
                    self.assertIsInstance(event, OrboniObject)
                    self.assertEqual(event.object, "event")
                else:
                    with self.assertRaises(SignatureVerificationError) as caught:
                        Webhook.construct_event(c["body"], c["header"], c["secret"], vectors["tolerance"], now=c["now"])
                    self.assertEqual(caught.exception.type, "signature_verification_error")

    def test_is_accepted_from_bytes_as_well_as_text(self) -> None:
        c = case("unicode body")
        event = Webhook.construct_event(c["body"].encode("utf-8"), c["header"], c["secret"], now=c["now"])
        self.assertIn("złoty", event.data.object.note)
        again = Webhook.construct_event(bytearray(c["body"].encode("utf-8")), c["header"], c["secret"], now=c["now"])
        self.assertEqual(again, event)

    def test_is_accepted_with_any_of_several_secrets(self) -> None:
        c = case("valid")
        self.assertIs(Webhook.verify_signature(c["body"], c["header"], ["whsec_other", c["secret"]], now=c["now"]), True)
        self.assertIs(Webhook.verify_signature(c["body"], c["header"], ("", c["secret"]), now=c["now"]), True)

    def test_takes_the_first_of_a_repeated_header(self) -> None:
        c = case("valid")
        Webhook.verify_signature(c["body"], [c["header"], "junk"], c["secret"], now=c["now"])
        with self.assertRaises(SignatureVerificationError):
            Webhook.verify_signature(c["body"], ["junk", c["header"]], c["secret"], now=c["now"])

    def test_is_checked_by_the_clients_own_webhooks_too_with_no_key_needed_for_it(self) -> None:
        c = case("valid")
        event = orboni.Orboni("sk_test_x").webhooks.construct_event(c["body"], c["header"], c["secret"], now=c["now"])
        self.assertEqual(event.type, "checkout.session.completed")
        self.assertEqual(event.data.object.metadata.order_id, "1042")


class AWebhook(unittest.TestCase):
    c = case("valid")

    def test_without_the_header_is_refused(self) -> None:
        headers: List[Any] = [None, "", "  ", []]
        for header in headers:
            with self.subTest(header=header):
                with self.assertRaisesRegex(SignatureVerificationError, "No Webhook-Signature header"):
                    Webhook.construct_event(self.c["body"], header, self.c["secret"], now=self.c["now"])

    def test_as_parsed_json_is_refused_with_a_word_on_why(self) -> None:
        with self.assertRaisesRegex(SignatureVerificationError, "raw request body"):
            Webhook.construct_event(json.loads(self.c["body"]), self.c["header"], self.c["secret"], now=self.c["now"])

    def test_outside_the_tolerance_says_by_how_much(self) -> None:
        with self.assertRaisesRegex(SignatureVerificationError, "301 s from now, more than the 300 s allowed"):
            Webhook.construct_event(self.c["body"], self.c["header"], self.c["secret"], now=self.c["now"] + 301)
        Webhook.construct_event(self.c["body"], self.c["header"], self.c["secret"], 600, now=self.c["now"] + 301)

    def test_needs_a_secret_and_a_real_tolerance(self) -> None:
        secrets: List[Any] = ["", [], None]
        for secret in secrets:
            with self.subTest(secret=secret):
                with self.assertRaises(ValueError):
                    Webhook.construct_event(self.c["body"], self.c["header"], secret, now=self.c["now"])
        for tolerance in (0, -1):
            with self.subTest(tolerance=tolerance):
                with self.assertRaises(ValueError):
                    Webhook.construct_event(self.c["body"], self.c["header"], self.c["secret"], tolerance, now=self.c["now"])
        with self.assertRaises(TypeError):
            Webhook.construct_event(self.c["body"], self.c["header"], self.c["secret"], "300", now=self.c["now"])  # type: ignore[arg-type]

    def test_signed_now_is_accepted_at_the_default_tolerance(self) -> None:
        t = int(time.time())
        header = f"t={t},v1={sign(self.c['secret'], t, self.c['body'])}"
        event = Webhook.construct_event(self.c["body"], header, self.c["secret"])
        self.assertEqual(event.id, "evt_3Fh8Kq2Lm9Np4Rs7Tv1Wx6Yz")

    def test_signed_but_not_json_is_refused(self) -> None:
        t = int(time.time())
        body = "not json"
        with self.assertRaisesRegex(SignatureVerificationError, "not JSON"):
            Webhook.construct_event(body, f"t={t},v1={sign(self.c['secret'], t, body)}", self.c["secret"])

    def test_with_a_signature_that_is_not_hex_is_refused_not_crashed(self) -> None:
        t = self.c["now"]
        with self.assertRaises(SignatureVerificationError):
            Webhook.construct_event(self.c["body"], f"t={t},v1=zażółć", self.c["secret"], now=t)


if __name__ == "__main__":
    unittest.main()
