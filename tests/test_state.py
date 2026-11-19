"""State reconstruction, and the two things that could make it a lie.

The first is ordering by arrival, which works on a sorted stream and quietly
produces a different answer on a shuffled one. The second is a tie the data
cannot settle, resolved by a sort that looks principled and is not.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from subevents import config, generator as gen, signature, state
from subevents.store import EventStore
from subevents.webhook import HANDLED_TYPES, WebhookHandler

import replay

LIVE_SECRET = config.FIXTURE_SECRETS[0]


@pytest.fixture(scope="module")
def streams():
    clean = gen.generate()
    return clean, gen.to_wire(clean)


@pytest.fixture(scope="module")
def logs(streams):
    clean, wire = streams
    return replay.deliver(clean), replay.deliver(wire)


@pytest.fixture(scope="module")
def probe_corpus():
    return gen.generate(gen.GeneratorConfig(same_second_update_rate=0.35))


def envelope(event_id, event_type, created, obj):
    return {
        "id": event_id,
        "object": "event",
        "created": created,
        "type": event_type,
        "data": {"object": obj},
    }


def subscription_object(sub_id, status, quantity=1, plan="price_team"):
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": "cus_one",
        "status": status,
        "current_period_start": 100,
        "current_period_end": 200,
        "items": {"data": [{"price": {"id": plan}, "quantity": quantity}]},
    }


def test_a_shuffled_duplicated_replay_reconstructs_the_same_state(logs):
    """The claim the whole day is for."""
    clean_log, wire_log = logs
    from_clean = state.reconstruct(clean_log)
    from_wire = state.reconstruct(wire_log)

    assert from_clean.digest() == from_wire.digest()
    assert from_clean.subscriptions == from_wire.subscriptions
    assert len(from_clean.subscriptions) == 200


def test_the_fold_dedupes_on_its_own_and_not_only_behind_the_store(streams, logs):
    """The store refuses a repeat, so the log never carries one.

    Reading straight off the wire is the only route that puts a duplicate in
    front of the reconstruction. Without this arm the dedupe in the fold is
    code nothing exercises.
    """
    clean, wire = streams
    clean_log, _ = logs

    from_stream = state.reconstruct(wire)
    assert from_stream.duplicates_dropped == len(wire) - len(clean)
    assert from_stream.digest() == state.reconstruct(clean_log).digest()


def test_the_wire_stream_really_was_shuffled_and_duplicated(streams):
    """Otherwise the test above passes on two identical inputs."""
    clean, wire = streams
    assert len(wire) > len(clean)
    assert [e["id"] for e in wire][: len(clean)] != [e["id"] for e in clean]


def test_reconstruction_ignores_the_order_the_log_is_walked_in(logs):
    clean_log, _ = logs
    forward = state.reconstruct(clean_log)
    backward = state.reconstruct(list(reversed(clean_log)))
    assert forward.digest() == backward.digest()


def test_ordering_by_arrival_gets_subscriptions_wrong(logs):
    """The naive reading, kept as a test so the data ordering is seen to earn it."""
    clean_log, wire_log = logs
    correct = state.reconstruct(clean_log)
    by_arrival = state.reconstruct(wire_log, key=None)

    wrong = state.differences(correct.subscriptions, by_arrival.subscriptions)
    assert len(wrong) > 50
    assert by_arrival.orphan_events > 0


def test_no_event_arrives_before_its_subscription_under_the_event_time_key(logs):
    clean_log, wire_log = logs
    assert state.reconstruct(clean_log).orphan_events == 0
    assert state.reconstruct(wire_log).orphan_events == 0


def test_duplicates_are_dropped_rather_than_applied_twice():
    sub = subscription_object("sub_a", "active")
    events = [
        envelope("evt_1", "customer.subscription.created", 10, sub),
        envelope("evt_2", "invoice.payment_succeeded", 20, {"id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "paid"}),
    ]
    once = state.reconstruct(events)
    thrice = state.reconstruct(events + events + events)

    assert thrice.duplicates_dropped == 4
    assert thrice.subscriptions["sub_a"].invoices_paid == 1
    assert once.digest() == thrice.digest()


def test_an_envelope_with_no_data_key_is_skipped_and_counted():
    """The handler accepts one of these on purpose. It must not crash the reader."""
    bare = {"id": "evt_bare", "object": "event", "created": 5, "type": "customer.subscription.updated"}
    out = state.reconstruct([bare])
    assert out.skipped_no_object == 1
    assert out.subscriptions == {}


@pytest.mark.parametrize(
    "data",
    [None, "a string", {"object": None}, {"object": "not a dict"}, {"object": {}}],
)
def test_an_unusable_data_block_is_skipped(data):
    event = {"id": "evt_x", "object": "event", "created": 5, "type": "customer.subscription.updated", "data": data}
    assert state.reconstruct([event]).skipped_no_object == 1


def test_a_known_history_reconstructs_field_for_field():
    """Every field asserted against a history small enough to work out by hand.

    The corpus tests compare one reconstruction against another, so a fold that
    is wrong in the same way on both sides agrees with itself. This one has the
    answer written out.
    """
    events = [
        envelope("evt_1", "customer.subscription.created", 100, subscription_object("sub_a", "active", quantity=2, plan="price_starter")),
        envelope("evt_2", "invoice.payment_succeeded", 110, {"id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "paid"}),
        envelope("evt_3", "invoice.payment_failed", 160, {"id": "in_2", "object": "invoice", "subscription": "sub_a", "status": "open"}),
        envelope("evt_4", "customer.subscription.updated", 160, subscription_object("sub_a", "past_due", quantity=3, plan="price_team")),
    ]
    out = state.reconstruct(events)

    assert out.subscriptions["sub_a"] == state.SubscriptionState(
        subscription_id="sub_a",
        customer_id="cus_one",
        status="past_due",
        plan="price_team",
        quantity=3,
        current_period_start=100,
        current_period_end=200,
        cancel_at_period_end=False,
        first_event_at=100,
        last_event_at=160,
        events_applied=4,
        invoices_paid=1,
        invoices_failed=1,
        last_invoice_id="in_2",
        last_invoice_status="open",
        ambiguous_seconds=0,
    )


def test_an_orphan_subscription_carries_empty_fields_rather_than_guesses():
    """What a subscription looks like when its creation event never arrived.

    This happens under arrival ordering and it is the only route that reads the
    field defaults, so without a test on it those defaults are nine numbers
    nobody has ever looked at.
    """
    invoice = envelope("evt_1", "invoice.payment_succeeded", 110, {
        "id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "paid",
    })
    out = state.reconstruct([invoice])

    assert out.orphan_events == 1
    orphan = out.subscriptions["sub_a"]
    assert orphan.status == ""
    assert orphan.plan == ""
    assert orphan.quantity == 0
    assert orphan.current_period_start == 0
    assert orphan.current_period_end == 0
    assert orphan.customer_id == ""
    assert orphan.invoices_paid == 1


def test_the_counts_on_a_reconstruction_add_up():
    """`applied` and `delivered` are reported and were never checked."""
    good = envelope("evt_1", "customer.subscription.created", 100, subscription_object("sub_a", "active"))
    bare = {"id": "evt_2", "object": "event", "created": 101, "type": "customer.subscription.updated"}

    out = state.reconstruct([good, bare, good])

    assert out.delivered == 3
    assert out.duplicates_dropped == 1
    assert out.applied == 1
    assert out.skipped_no_object == 1
    assert out.applied + out.skipped_no_object + out.duplicates_dropped == out.delivered


def test_an_event_naming_a_subscription_that_is_not_a_string_is_refused():
    """Stripe sends a string. Anything else is a sender this code should not trust."""
    for bad in (1234, ["sub_a"], {"id": "sub_a"}, True):
        event = envelope("evt_1", "customer.subscription.updated", 100, {"object": "invoice", "id": "in_1", "subscription": bad})
        assert state.reconstruct([event]).skipped_no_object == 1


def test_an_empty_subscription_id_is_refused():
    event = envelope("evt_1", "customer.subscription.updated", 100, {"object": "subscription", "id": ""})
    assert state.reconstruct([event]).skipped_no_object == 1


def test_a_subscription_object_with_no_price_leaves_the_plan_alone():
    """An item carrying no price is not a reason to forget which plan it was on."""
    created = envelope("evt_1", "customer.subscription.created", 100, subscription_object("sub_a", "active", plan="price_team"))
    stripped = envelope("evt_2", "customer.subscription.updated", 200, {
        "id": "sub_a",
        "object": "subscription",
        "status": "active",
        "items": {"data": [{"quantity": 4}]},
    })

    out = state.reconstruct([created, stripped])
    assert out.subscriptions["sub_a"].plan == "price_team"
    assert out.subscriptions["sub_a"].quantity == 4


def test_every_handled_event_type_has_a_rank():
    """A type the handler stores and the reader has no opinion about sorts last.

    That is survivable once. It is not survivable silently, because the event
    would land after everything else in its second and change the answer.
    """
    assert set(HANDLED_TYPES) == set(state.TYPE_RANK)


def test_an_unknown_type_sorts_after_everything_ranked():
    assert state.UNRANKED > max(state.TYPE_RANK.values())
    unknown = envelope("evt_u", "customer.subscription.trial_will_end", 10, subscription_object("sub_a", "active"))
    created = envelope("evt_c", "customer.subscription.created", 10, subscription_object("sub_a", "active"))
    ordered = sorted([unknown, created], key=state.event_order_key)
    assert [e["id"] for e in ordered] == ["evt_c", "evt_u"]


def test_the_rank_table_produces_this_exact_order_inside_one_second():
    """Pins every rank against its neighbour and not just the pair that matters.

    Written after a mutation pass moved seven of the constants in TYPE_RANK one
    at a time and the suite noticed none of them. The corpus holds one tie
    shape, so every other rank was a number nothing read.

    The ids run backwards against the rank on purpose. If they ran with it, a
    constant nudged into its neighbour's rank would collide and the id would
    put the pair back in the order it was already in, and the test would pass
    on a table that had changed. The first version of this did exactly that.
    """
    in_rank_order = [
        "customer.subscription.created",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
        "customer.subscription.updated",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "customer.subscription.deleted",
    ]
    events = [
        envelope(f"evt_{len(in_rank_order) - i}", event_type, 500, subscription_object("sub_a", "active"))
        for i, event_type in enumerate(in_rank_order)
    ]

    ordered = [e["type"] for e in sorted(events, key=state.event_order_key)]
    assert ordered == [
        "customer.subscription.created",
        # These two share a rank, so the id decides and the pair reads
        # backwards against the list above. That is the rule working.
        "invoice.payment_failed",
        "invoice.payment_succeeded",
        "customer.subscription.updated",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "customer.subscription.deleted",
    ]


def test_the_two_invoice_events_share_a_rank_on_purpose():
    """They are the same kind of fact about the same second.

    Nothing in Stripe says a paid invoice precedes a failed one, so giving them
    different ranks would be inventing an order. The consequence is that a
    second holding both falls through to the id, and `ambiguous_seconds` counts
    it.
    """
    assert (
        state.TYPE_RANK["invoice.payment_succeeded"]
        == state.TYPE_RANK["invoice.payment_failed"]
    )
    events = [
        envelope("evt_b", "invoice.payment_succeeded", 20, {"id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "paid"}),
        envelope("evt_a", "invoice.payment_failed", 20, {"id": "in_2", "object": "invoice", "subscription": "sub_a", "status": "open"}),
    ]
    assert state.reconstruct(events).ambiguous_seconds == 1


def test_an_unranked_type_sorts_after_every_ranked_one_in_the_same_second():
    """UNRANKED is a number and the only thing that matters about it is that it wins."""
    ranked = [
        envelope(f"evt_z{i}", event_type, 500, subscription_object("sub_a", "active"))
        for i, event_type in enumerate(sorted(state.TYPE_RANK))
    ]
    unknown = envelope("evt_a", "customer.subscription.trial_will_end", 500, subscription_object("sub_a", "active"))

    ordered = sorted(ranked + [unknown], key=state.event_order_key)
    assert ordered[-1]["id"] == "evt_a"


def test_an_invoice_event_does_not_move_the_subscription_status():
    """The causal story behind the type rank, pinned.

    If an invoice event wrote status, ranking it before the update in the same
    second would be wrong rather than merely unproven.
    """
    events = [
        envelope("evt_1", "customer.subscription.created", 10, subscription_object("sub_a", "active")),
        envelope("evt_2", "invoice.payment_failed", 20, {"id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "open"}),
    ]
    out = state.reconstruct(events)
    assert out.subscriptions["sub_a"].status == "active"
    assert out.subscriptions["sub_a"].invoices_failed == 1
    assert out.subscriptions["sub_a"].last_invoice_status == "open"


def test_a_failed_payment_and_its_status_change_land_in_the_right_order():
    """Both at second 20. The invoice is the cause and goes first."""
    events = [
        envelope("evt_1", "customer.subscription.created", 10, subscription_object("sub_a", "active")),
        envelope("evt_3", "customer.subscription.updated", 20, subscription_object("sub_a", "past_due")),
        envelope("evt_2", "invoice.payment_failed", 20, {"id": "in_1", "object": "invoice", "subscription": "sub_a", "status": "open"}),
    ]
    ordered = sorted(events, key=state.event_order_key)
    assert [e["type"] for e in ordered][-2:] == ["invoice.payment_failed", "customer.subscription.updated"]
    assert state.reconstruct(events).subscriptions["sub_a"].status == "past_due"


def test_every_planted_tie_is_settled_by_the_type_rank(streams):
    clean, _ = streams
    resolution = state.tie_resolution(clean)
    assert resolution["tied_seconds"] == 73
    assert resolution["resolved_by_type_rank"] == resolution["tied_seconds"]
    assert resolution["resolved_by_event_id"] == 0


def test_reversing_the_type_precedence_changes_nothing_on_this_corpus(logs):
    """Recorded because it is the uncomfortable result and not the tidy one.

    Every tie in the published corpus is an invoice event beside a subscription
    update, and those two write different fields. The precedence rule is
    written down, argued for, and does no work here. A test asserting it
    mattered would be asserting something false.
    """
    clean_log, _ = logs
    forward = state.reconstruct(clean_log)
    backward = state.reconstruct(clean_log, key=state.reversed_tie_key)
    assert state.differences(forward.subscriptions, backward.subscriptions) == []


def test_two_updates_in_one_second_are_flagged_rather_than_quietly_resolved():
    events = [
        envelope("evt_1", "customer.subscription.created", 10, subscription_object("sub_a", "active", quantity=1)),
        envelope("evt_b", "customer.subscription.updated", 20, subscription_object("sub_a", "active", quantity=2)),
        envelope("evt_a", "customer.subscription.updated", 20, subscription_object("sub_a", "active", quantity=3)),
    ]
    out = state.reconstruct(events)
    assert out.ambiguous_seconds == 1
    assert out.subscriptions["sub_a"].ambiguous_seconds == 1
    # The id sort decided this and nothing in the data did.
    assert out.subscriptions["sub_a"].quantity == 2


def test_an_ambiguous_second_is_still_deterministic():
    events = [
        envelope("evt_1", "customer.subscription.created", 10, subscription_object("sub_a", "active")),
        envelope("evt_b", "customer.subscription.updated", 20, subscription_object("sub_a", "active", quantity=2)),
        envelope("evt_a", "customer.subscription.updated", 20, subscription_object("sub_a", "active", quantity=3)),
    ]
    first = state.reconstruct(events).digest()
    second = state.reconstruct(list(reversed(events))).digest()
    assert first == second


def test_a_second_holding_one_event_is_not_ambiguous(streams):
    clean, _ = streams
    out = state.reconstruct(clean)
    assert out.ambiguous_seconds == 0
    assert all(s.ambiguous_seconds == 0 for s in out.subscriptions.values())


def test_the_flag_misses_nothing_the_id_order_can_move(probe_corpus):
    """On the arm that plants same rank ties, every state the tie break moves is flagged.

    The flag fires wider than the cost, because most ties land on a field the
    second event rewrites anyway. Over reporting is the right direction for
    this. A missed one is a field decided by a coin flip with nothing saying so.
    """
    primary = state.reconstruct(probe_corpus)
    flipped = state.reconstruct(probe_corpus, key=replay.id_descending_key)

    moved = set(state.differences(primary.subscriptions, flipped.subscriptions))
    flagged = {k for k, v in primary.subscriptions.items() if v.ambiguous_seconds}

    assert moved, "the probe arm has to actually move something or it grades nothing"
    assert moved <= flagged


def test_the_probe_arm_still_replays_identically(probe_corpus):
    """Ambiguity is not an excuse for the shuffled replay to disagree."""
    wire = gen.to_wire(probe_corpus)
    assert state.reconstruct(probe_corpus).digest() == state.reconstruct(wire).digest()


def test_the_default_generator_plants_no_same_rank_ties(streams):
    """So the probe config is doing something the published corpus cannot."""
    clean, _ = streams
    assert state.tie_resolution(clean)["resolved_by_event_id"] == 0
    probe = gen.generate(gen.GeneratorConfig(same_second_update_rate=0.35))
    assert state.tie_resolution(probe)["resolved_by_event_id"] > 0


def test_the_state_digest_does_not_depend_on_dictionary_order():
    """The digest sorts by id, so two readers holding the same states agree.

    `reconstruct` sorts before it folds, so it cannot produce the two orders
    this needs. They are built by hand for that reason.
    """
    one = state.SubscriptionState(subscription_id="sub_a", status="active")
    two = state.SubscriptionState(subscription_id="sub_b", status="paused")

    forward = {"sub_a": one, "sub_b": two}
    backward = {"sub_b": two, "sub_a": one}

    assert list(forward) != list(backward)
    assert state.state_digest(forward) == state.state_digest(backward)


def test_the_store_hands_back_what_was_signed_and_delivered(streams):
    """The log read is the real path and not a list kept in memory."""
    clean, _ = streams
    sample = clean[:40]
    store = EventStore()
    handler = WebhookHandler(store, LIVE_SECRET)
    for event in sample:
        body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
        stamp = int(event["created"])
        handler.handle(body, signature.sign(body, LIVE_SECRET, stamp), stamp + 1)
    envelopes = store.stored_envelopes()
    store.close()

    assert [e["id"] for e in envelopes] == [e["id"] for e in sample]
    assert envelopes[0] == sample[0]
