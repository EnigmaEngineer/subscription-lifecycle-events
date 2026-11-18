import base64
import glob
import json
import os

import pytest

from subevents import config, signature
from subevents.store import EventStore
from subevents.webhook import HANDLED_TYPES, WebhookHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE, ROTATED = config.FIXTURE_SECRETS

FIXTURES = sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json")))


def fresh():
    store = EventStore()
    return store, WebhookHandler(store, [LIVE, ROTATED])


def fixture_ids():
    return [os.path.basename(p)[: -len(".json")] for p in FIXTURES]


def test_the_corpus_is_not_empty():
    # A parametrised suite over an empty glob passes and grades nothing.
    assert len(FIXTURES) >= 20


@pytest.mark.parametrize("path", FIXTURES, ids=fixture_ids())
def test_fixture_outcome(path):
    """Each fixture states its own expected status and outcome."""
    with open(path, encoding="utf-8") as handle:
        fixture = json.load(handle)
    body = base64.b64decode(fixture["body_b64"])
    store, handler = fresh()
    response = handler.handle(body, fixture["header"], fixture["now"])
    assert response.status == fixture["expect"]["status"], fixture["note"]
    assert response.outcome == fixture["expect"]["outcome"], fixture["note"]
    store.close()


def test_every_outcome_class_is_covered_by_the_corpus():
    outcomes = set()
    for path in FIXTURES:
        with open(path, encoding="utf-8") as handle:
            outcomes.add(json.load(handle)["expect"]["outcome"])
    # No single request can be a duplicate, so that class belongs to the
    # idempotency suite and not to this corpus.
    assert outcomes == {"accepted", "ignored", "rejected", "unparseable"}


def test_a_rejected_request_stores_nothing():
    store, handler = fresh()
    body = json.dumps(
        {"id": "evt_never", "type": "customer.subscription.created", "created": 1763000000}
    ).encode()
    response = handler.handle(body, "t=1763000000,v1=deadbeef", 1763000001)
    assert response.status == 400
    assert store.event_count() == 0


def test_an_ignored_type_stores_nothing_and_is_acknowledged():
    store, handler = fresh()
    body = json.dumps({"id": "evt_ref", "type": "charge.refunded", "created": 1763000000}).encode()
    header = signature.sign(body, LIVE, 1763000000)
    response = handler.handle(body, header, 1763000001)
    assert response.acknowledged
    assert response.outcome == "ignored"
    assert store.event_count() == 0


def test_all_handled_types_are_accepted():
    for event_type in sorted(HANDLED_TYPES):
        store, handler = fresh()
        body = json.dumps(
            {
                "id": "evt_" + event_type.replace(".", "_"),
                "type": event_type,
                "created": 1763000000,
                "data": {"object": {"id": "sub_1"}},
            }
        ).encode()
        header = signature.sign(body, LIVE, 1763000000)
        response = handler.handle(body, header, 1763000001)
        assert response.outcome == "accepted", event_type
        assert store.event_count() == 1
        store.close()


def test_the_stored_body_is_the_bytes_that_were_signed():
    store, handler = fresh()
    body = json.dumps(
        {"id": "evt_bytes", "type": "customer.subscription.created", "created": 1763000000}
    ).encode()
    header = signature.sign(body, LIVE, 1763000000)
    handler.handle(body, header, 1763000001)
    stored = store.conn.execute("SELECT body FROM events WHERE event_id = ?", ("evt_bytes",)).fetchone()[0]
    # Re-signing what came out of the database has to give the same header back.
    assert signature.sign(stored, LIVE, 1763000000) == header


def test_received_at_is_the_signed_timestamp_and_not_our_clock():
    store, handler = fresh()
    body = json.dumps(
        {"id": "evt_clock", "type": "customer.subscription.created", "created": 1762999000}
    ).encode()
    header = signature.sign(body, LIVE, 1763000000)
    handler.handle(body, header, 1763000250)
    row = store.conn.execute(
        "SELECT created, received_at FROM events WHERE event_id = ?", ("evt_clock",)
    ).fetchone()
    assert row == (1762999000, 1763000000)
