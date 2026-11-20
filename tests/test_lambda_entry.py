"""The gateway event is not a request, and the difference is the signature.

Everything the verifier does rests on having the exact bytes the sender signed.
API Gateway hands Lambda a JSON document instead, with the body as a string and
a flag saying whether that string is base64. These tests drive the fixture
corpus through both shapes of that document and insist the outcome is the one
the fixture already carries.

The last test is the interesting one. It runs the corpus through a decoder that
ignores the flag, which is the shape this module exists to prevent, and counts
what that costs.
"""

from __future__ import annotations

import base64
import glob
import json
import os

import pytest

from subevents import config, lambda_entry
from subevents.store import EventStore
from subevents.webhook import WebhookHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json")))

SECRETS = list(config.FIXTURE_SECRETS)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def gateway_event(body: bytes, header: str, encoded: bool, header_name="stripe-signature"):
    """A payload format 2.0 proxy event carrying one delivery."""
    return {
        "version": "2.0",
        "routeKey": "POST /stripe/events",
        "rawPath": "/stripe/events",
        "headers": {"content-type": "application/json", header_name: header},
        "requestContext": {"http": {"method": "POST", "path": "/stripe/events"}},
        "body": base64.b64encode(body).decode("ascii") if encoded else body.decode("utf-8"),
        "isBase64Encoded": encoded,
    }


def fresh_handler():
    return WebhookHandler(EventStore(), SECRETS)


def arrives_as_text(body: bytes) -> bool:
    """Whether the gateway could hand this body over as a string at all.

    One fixture in the corpus is signed bytes that are not valid in this
    encoding, and the gateway has no way to put those in a JSON string. It would
    set the flag and send base64. So the text arm is not a shape that fixture can
    arrive in, and asserting an outcome for it would be asserting something about
    a request nobody can make.
    """
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(p) for p in FIXTURES])
@pytest.mark.parametrize("encoded", [False, True], ids=["body-as-text", "body-as-base64"])
def test_the_fixture_outcome_survives_the_gateway(path, encoded):
    fixture = load(path)
    body = base64.b64decode(fixture["body_b64"])
    if not encoded and not arrives_as_text(body):
        pytest.skip("this body cannot be carried as a string, so the gateway would flag it")
    event = gateway_event(body, fixture["header"], encoded)

    response = lambda_entry.handle_event(event, fresh_handler(), fixture["now"])
    payload = json.loads(response["body"])

    assert response["statusCode"] == fixture["expect"]["status"], fixture["name"]
    assert payload["outcome"] == fixture["expect"]["outcome"], fixture["name"]


def test_exactly_one_fixture_cannot_be_carried_as_text():
    """Pinned as a count so the skip above cannot quietly grow.

    A skip that nobody counts is a test that stopped running. If a later fixture
    lands with bytes that are not valid in this encoding, this fails and somebody
    has to decide whether the text arm still means anything.
    """
    bodies = [base64.b64decode(load(path)["body_b64"]) for path in FIXTURES]
    as_text = [body for body in bodies if arrives_as_text(body)]
    assert len(bodies) == 23
    assert len(as_text) == 22


def test_a_body_that_is_not_utf8_only_arrives_base64():
    """The one case the text path cannot carry at all.

    Stripe sends UTF-8 JSON, so this is not a delivery anybody expects. It is
    here because the text path silently handles every body that happens to be
    valid in this encoding, which is how a decoder that is wrong looks right for
    months.
    """
    body = b'{"id":"evt_x","type":"customer.subscription.created","created":1,"raw":"\xff\xfe"}'
    with pytest.raises(UnicodeDecodeError):
        body.decode("utf-8")
    event = gateway_event(body, "t=1,v1=deadbeef", encoded=True)
    assert lambda_entry.raw_body(event) == body


def test_the_header_is_found_whatever_its_case():
    body = b"{}"
    for name in ("stripe-signature", "Stripe-Signature", "STRIPE-SIGNATURE"):
        event = gateway_event(body, "t=1,v1=x", encoded=False, header_name=name)
        assert lambda_entry.signature_header(event["headers"]) == "t=1,v1=x"


def test_a_request_with_no_signature_header_is_refused_without_reading_the_body():
    event = {"body": "{}", "isBase64Encoded": False, "headers": {"content-type": "x"}}
    response = lambda_entry.handle_event(event, fresh_handler(), 1.0)
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["outcome"] == "malformed"


def test_a_body_flagged_base64_that_is_not_base64_is_refused():
    event = gateway_event(b"{}", "t=1,v1=x", encoded=False)
    event["isBase64Encoded"] = True
    event["body"] = "not base64 at all"
    response = lambda_entry.handle_event(event, fresh_handler(), 1.0)
    assert response["statusCode"] == 400
    assert "base64" in json.loads(response["body"])["reason"]


def test_two_signature_headers_merge_and_the_duplicate_timestamp_refuses_them():
    """What the gateway does to a repeated header, and what saves us from it.

    A repeated header arrives as one value joined by a comma, which is also how
    a rotation header carries two signatures. So a second header is
    indistinguishable from a second signature by shape alone. The timestamp is
    what separates them, because a whole second header brings its own `t` and
    the parser refuses more than one of those.

    That refusal was written for a different reason and it is the only thing
    standing here, which is worth knowing before somebody relaxes it.
    """
    fixture = load(os.path.join(ROOT, "fixtures", "signed", "valid-single-secret.json"))
    body = base64.b64decode(fixture["body_b64"])
    merged = fixture["header"] + "," + fixture["header"]
    event = gateway_event(body, merged, encoded=False)

    response = lambda_entry.handle_event(event, fresh_handler(), fixture["now"])
    assert response["statusCode"] == 400
    assert "timestamp" in json.loads(response["body"])["reason"]


def test_a_second_header_carrying_only_signatures_is_accepted_as_a_rotation():
    """And the case that does get through, which costs nothing.

    An appended `v1` with no timestamp merges cleanly and reads as a rotation.
    It is accepted when any one of the signatures verifies, so an attacker
    gains nothing by appending a value they could not compute. Stated here
    because it looks alarming and is not.
    """
    fixture = load(os.path.join(ROOT, "fixtures", "signed", "valid-single-secret.json"))
    body = base64.b64decode(fixture["body_b64"])
    event = gateway_event(body, fixture["header"] + ",v1=" + "0" * 64, encoded=False)

    response = lambda_entry.handle_event(event, fresh_handler(), fixture["now"])
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["outcome"] == "accepted"


def naive_raw_body(event):
    """The decoder this module exists to not be. Used only by the test below."""
    return event["body"].encode("utf-8")


def test_ignoring_the_base64_flag_makes_every_delivery_look_like_an_attacker():
    """The measurement, and the shape of it matters more than the count.

    Under the naive decoder every one of the 23 fixtures comes back `rejected`.
    Not fewer accepted deliveries. No other outcome at all. Eleven of them
    changed to get there and twelve were already rejected for a real signature
    reason, so a monitor counting refusals sees a number that went up and a
    reason that did not change.

    Five valid deliveries, one event this platform ignores on purpose and five
    malformed envelopes all report that no signature matched, which points
    whoever is holding the pager at Stripe rather than at the gateway.
    """
    outcomes = {"real": [], "naive": []}
    for path in FIXTURES:
        fixture = load(path)
        body = base64.b64decode(fixture["body_b64"])
        event = gateway_event(body, fixture["header"], encoded=True)

        real = fresh_handler().handle(
            lambda_entry.raw_body(event), fixture["header"], fixture["now"]
        )
        naive = fresh_handler().handle(
            naive_raw_body(event), fixture["header"], fixture["now"]
        )
        outcomes["real"].append(real.outcome)
        outcomes["naive"].append(naive.outcome)

    changed = sum(1 for a, b in zip(outcomes["real"], outcomes["naive"]) if a != b)

    assert set(outcomes["naive"]) == {"rejected"}
    assert changed == 11
    assert len(outcomes["real"]) == 23
    assert sorted(set(outcomes["real"])) == ["accepted", "ignored", "rejected", "unparseable"]


# Four survivors from the mutation pass on 2026-11-20, and none of them turned
# out to be equivalent. Each test below is the one that kills one of them.


def test_headers_that_are_not_a_mapping_are_refused_rather_than_crashing():
    """A truthy non mapping took the loop and raised something nobody catches.

    The guard reads `not a dict or empty`. With `and` in place of `or` a string
    or a list reaches `headers.items()` and the AttributeError is not a
    GatewayRequestError, so it leaves the function as a 500 instead of a 400.
    The gateway builds this map and should never send one, which is exactly the
    reasoning that leaves a path ungraded.
    """
    for headers in ("stripe-signature: t=1", ["stripe-signature"], 7):
        with pytest.raises(lambda_entry.GatewayRequestError):
            lambda_entry.signature_header(headers)


def test_an_empty_header_map_is_refused_before_the_loop():
    with pytest.raises(lambda_entry.GatewayRequestError):
        lambda_entry.signature_header({})


def test_base64_decoding_refuses_characters_outside_the_alphabet():
    """Without strict validation the decoder discards them and returns wrong bytes.

    `validate=False` is the default and it silently drops anything outside the
    alphabet, so a corrupted body decodes to something plausible and the
    signature then fails. That reports `rejected`, which is the reading that
    sends somebody hunting for an attacker. A body that is not base64 has to
    come back as malformed instead.
    """
    corrupted = "QUJD\nRA=="
    assert base64.b64decode(corrupted) == b"ABCD"
    with pytest.raises(lambda_entry.GatewayRequestError):
        lambda_entry.raw_body({"body": corrupted, "isBase64Encoded": True})


def test_the_response_is_not_flagged_as_base64():
    """The flag on the way out means the same thing it means on the way in.

    Flagged true, the gateway decodes our JSON body before returning it and
    Stripe gets bytes nobody wrote. Nothing read this field until the mutation
    pass flipped it.
    """
    response = lambda_entry.proxy_response(200, "accepted")
    assert response["isBase64Encoded"] is False
    assert json.loads(response["body"]) == {"outcome": "accepted"}


def test_the_ledger_path_comes_from_the_environment_and_falls_back(monkeypatch):
    """`build_handler` had no test at all, which is why the fallback survived.

    With `and` in place of `or` an unset variable yields an empty path and a
    configured one is ignored. Both are silent. This is the entry point the
    deployed function calls and nothing was calling it here.
    """
    monkeypatch.delenv(lambda_entry.LEDGER_PATH_VAR, raising=False)
    assert lambda_entry.ledger_path() == lambda_entry.DEFAULT_LEDGER_PATH

    monkeypatch.setenv(lambda_entry.LEDGER_PATH_VAR, ":memory:")
    assert lambda_entry.ledger_path() == ":memory:"

    # Whitespace only is the case that separates `or` from `and` on the strip.
    monkeypatch.setenv(lambda_entry.LEDGER_PATH_VAR, "   ")
    assert lambda_entry.ledger_path() == lambda_entry.DEFAULT_LEDGER_PATH


def test_build_handler_opens_the_ledger_it_was_told_to(monkeypatch):
    monkeypatch.setenv(lambda_entry.LEDGER_PATH_VAR, ":memory:")
    handler = lambda_entry.build_handler()
    body = b'{"id":"evt_b","type":"customer.subscription.created","created":1763000000,"data":{"object":{"id":"sub_b"}}}'
    from subevents import signature as sig

    header = sig.sign(body, config.FIXTURE_SECRETS[0], 1763000000)
    assert handler.handle(body, header, 1763000001).outcome == "accepted"
