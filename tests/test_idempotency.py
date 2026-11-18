import base64
import json
import os

from subevents import config, signature
from subevents.store import Decision, EventStore
from subevents.webhook import WebhookHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE, ROTATED = config.FIXTURE_SECRETS


def event(event_id="evt_dup", created=1763000000, amount=1900):
    return json.dumps(
        {
            "id": event_id,
            "type": "invoice.payment_succeeded",
            "created": created,
            "data": {"object": {"id": "in_1", "amount_paid": amount}},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_nine_retries_leave_one_row():
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    body = event()
    header = signature.sign(body, LIVE, 1763000000)

    outcomes = [handler.handle(body, header, 1763000001).outcome for _ in range(10)]

    assert outcomes[0] == "accepted"
    assert outcomes[1:] == ["duplicate"] * 9
    assert store.event_count() == 1
    assert store.delivery_total() == 10
    assert store.conflict_total() == 0


def test_the_signature_header_is_not_part_of_the_identity():
    """Stripe re-signs a retry with a new timestamp. It is the same event."""
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    body = event()

    first = handler.handle(body, signature.sign(body, LIVE, 1763000000), 1763000001)
    second = handler.handle(body, signature.sign(body, LIVE, 1763000120), 1763000121)

    assert first.outcome == "accepted"
    assert second.outcome == "duplicate"
    assert store.event_count() == 1


def test_a_retry_signed_with_the_rotated_secret_is_still_a_duplicate():
    store = EventStore()
    handler = WebhookHandler(store, [LIVE, ROTATED])
    body = event()

    assert handler.handle(body, signature.sign(body, LIVE, 1763000000), 1763000001).outcome == "accepted"
    assert handler.handle(body, signature.sign(body, ROTATED, 1763000060), 1763000061).outcome == "duplicate"
    assert store.event_count() == 1


def test_same_id_different_body_is_a_conflict_and_the_first_body_survives():
    store = EventStore()
    handler = WebhookHandler(store, LIVE)

    original = event(amount=1900)
    altered = event(amount=9900)
    handler.handle(original, signature.sign(original, LIVE, 1763000000), 1763000001)
    response = handler.handle(altered, signature.sign(altered, LIVE, 1763000060), 1763000061)

    assert response.outcome == "conflict"
    assert response.acknowledged
    assert store.event_count() == 1
    stored = store.conn.execute("SELECT body FROM events WHERE event_id = ?", ("evt_dup",)).fetchone()[0]
    assert stored == original
    assert store.conflict_total() == 1


def test_a_conflict_does_not_bump_the_delivery_count():
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    original = event(amount=1900)
    altered = event(amount=9900)
    handler.handle(original, signature.sign(original, LIVE, 1763000000), 1763000001)
    handler.handle(altered, signature.sign(altered, LIVE, 1763000060), 1763000061)
    count = store.conn.execute(
        "SELECT delivery_count FROM deliveries WHERE event_id = ?", ("evt_dup",)
    ).fetchone()[0]
    # A conflict is not a delivery of the thing we stored, so it is counted apart.
    assert count == 1


def test_distinct_ids_are_distinct_events():
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    for i in range(50):
        body = event(event_id=f"evt_{i}")
        handler.handle(body, signature.sign(body, LIVE, 1763000000), 1763000001)
    assert store.event_count() == 50
    assert store.delivery_total() == 50


def test_the_two_inserts_are_one_transaction():
    """A failing events insert must not leave a dedupe row behind.

    Forced by breaking the events table's uniqueness out from under the store,
    which is the only way to make the second insert fail without changing the
    code under test.
    """
    store = EventStore()
    body = event(event_id="evt_tx")
    store.conn.execute(
        "INSERT INTO events (event_id, event_type, created, received_at, body)"
        " VALUES (?, ?, ?, ?, ?)",
        ("evt_tx", "invoice.payment_succeeded", 1763000000, 1763000000, b"planted"),
    )

    raised = False
    try:
        store.accept("evt_tx", "invoice.payment_succeeded", 1763000000, 1763000000, body)
    except Exception:
        raised = True

    assert raised
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM deliveries WHERE event_id = ?", ("evt_tx",)
    ).fetchone()[0]
    assert rows == 0


def test_the_whole_fixture_corpus_against_one_endpoint():
    """Five valid fixtures, one event id, one stored row."""
    import glob

    store = EventStore()
    handler = WebhookHandler(store, [LIVE, ROTATED])
    decisions = []
    for path in sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            fixture = json.load(handle)
        body = base64.b64decode(fixture["body_b64"])
        decisions.append(handler.handle(body, fixture["header"], fixture["now"]).outcome)

    assert decisions.count("accepted") == 1
    assert decisions.count("duplicate") == 4
    assert store.event_count() == 1


def test_decision_enum_values_are_stable():
    # These strings reach a database column, so renaming one is a migration.
    assert [d.value for d in Decision] == ["new", "duplicate", "conflict"]


def test_a_file_backed_store_survives_being_reopened(tmp_path):
    """Every other test here runs against :memory:, where the WAL pragma is a no op.

    An endpoint that forgets its ledger on restart has no idempotency at all, so
    the file path gets exercised at least once.
    """
    path = str(tmp_path / "events.db")
    first = EventStore(path)
    handler = WebhookHandler(first, LIVE)
    body = event(event_id="evt_restart")
    header = signature.sign(body, LIVE, 1763000000)
    assert handler.handle(body, header, 1763000001).outcome == "accepted"
    first.close()

    second = EventStore(path)
    reopened = WebhookHandler(second, LIVE)
    assert reopened.handle(body, header, 1763000002).outcome == "duplicate"
    assert second.event_count() == 1
    assert second.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    second.close()


def test_an_envelope_with_no_data_is_accepted_today():
    """Documenting current behaviour rather than endorsing it.

    The shape check reads id, type and created and deliberately does not look
    inside data, because pinning Stripe's object shape would turn a product
    change into a 400. The cost is that a consumer reading data.object has to
    handle its absence, and nothing downstream does yet.
    """
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    body = json.dumps(
        {"id": "evt_nodata", "type": "customer.subscription.created", "created": 1763000000},
        separators=(",", ":"),
    ).encode()
    response = handler.handle(body, signature.sign(body, LIVE, 1763000000), 1763000001)
    assert response.outcome == "accepted"
    stored = json.loads(
        store.conn.execute("SELECT body FROM events WHERE event_id = ?", ("evt_nodata",)).fetchone()[0]
    )
    assert "data" not in stored


def test_the_ledger_grows_with_every_distinct_id():
    """There is no retention window and this is the test that says so out loud."""
    store = EventStore()
    handler = WebhookHandler(store, LIVE)
    for i in range(200):
        body = event(event_id=f"evt_growth_{i}")
        handler.handle(body, signature.sign(body, LIVE, 1763000000), 1763000001)
    rows = store.conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert rows == 200
