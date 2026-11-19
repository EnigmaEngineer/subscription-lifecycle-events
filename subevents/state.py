"""Reconstruct subscription state from the event log.

The log is append only and the rows arrive shuffled with duplicates in them, so
the sequence a row was written under says nothing about the order the events
happened in. Reconstruction sorts on fields the event itself carries and never
on the sequence the store handed out. That is the whole reason a shuffled
replay can produce the same state as a clean one.

Stripe has no version counter on a subscription object. Two events for one
subscription inside one second therefore have no total order anywhere in the
data, and the generator plants that case rather than hiding it. The rule used
here is a precedence over event types inside a tied second. It is written down
in TYPE_RANK with the reasoning beside it. Where the type rank also ties, the
event id breaks it, and an id is random so that last step is a coin flip that
happens to be repeatable. It is there to make the ordering total rather than
because it carries any meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# Precedence inside one second. The order is causal rather than alphabetical.
#
# A subscription has to exist before anything can update it, so created is
# first. An invoice event is the cause of the subscription update that follows
# it. Stripe reports the failed payment and moves the subscription to past_due
# in the same second, and applying the update first would leave the invoice
# counters describing a state that had already moved on. A pause lands before
# the resume that ends it. A delete is terminal so it goes last.
TYPE_RANK = {
    "customer.subscription.created": 0,
    "invoice.payment_succeeded": 1,
    "invoice.payment_failed": 1,
    "customer.subscription.updated": 2,
    "customer.subscription.paused": 3,
    "customer.subscription.resumed": 4,
    "customer.subscription.deleted": 5,
}

# Anything the handler stored but this module has no opinion about sorts after
# everything it does. A new Stripe event type should not silently jump the queue.
UNRANKED = 90

INVOICE_EVENTS = frozenset({"invoice.payment_succeeded", "invoice.payment_failed"})


def event_order_key(event: dict) -> tuple:
    """The order events are applied in. Nothing in it comes from arrival."""
    return (
        int(event["created"]),
        TYPE_RANK.get(event["type"], UNRANKED),
        event["id"],
    )


def reversed_tie_key(event: dict) -> tuple:
    """`event_order_key` with the type precedence inverted.

    Only the inside of a tied second changes, because `created` dominates the
    key. Reconstructing twice under the two keys and diffing is how the cost of
    the tie break gets measured instead of argued about.
    """
    return (
        int(event["created"]),
        -TYPE_RANK.get(event["type"], UNRANKED),
        event["id"],
    )


@dataclass
class SubscriptionState:
    subscription_id: str
    customer_id: str = ""
    status: str = ""
    plan: str = ""
    quantity: int = 0
    current_period_start: int = 0
    current_period_end: int = 0
    cancel_at_period_end: bool = False
    first_event_at: int = 0
    last_event_at: int = 0
    events_applied: int = 0
    invoices_paid: int = 0
    invoices_failed: int = 0
    last_invoice_id: str = ""
    last_invoice_status: str = ""
    # Seconds where this subscription had two events the type rank could not
    # separate. The state is still produced and one of its fields was decided
    # by a sort on a random id. A consumer that cares reads this first.
    ambiguous_seconds: int = 0


@dataclass
class Reconstruction:
    subscriptions: dict[str, SubscriptionState] = field(default_factory=dict)
    delivered: int = 0
    duplicates_dropped: int = 0
    applied: int = 0
    skipped_no_object: int = 0
    orphan_events: int = 0
    ambiguous_seconds: int = 0

    def digest(self) -> str:
        return state_digest(self.subscriptions)


def subject_of(event: dict) -> tuple[str, dict]:
    """Return the subscription id this event is about and the object carrying it.

    Returns an empty id when the envelope has nothing usable in it. The handler
    accepts an envelope with no `data` key on purpose, because pinning Stripe's
    object shape in the shape check would turn a product change into a 400. The
    cost of that decision lands here, so this function reads defensively and
    never assumes `data.object` is present.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return "", {}
    obj = data.get("object")
    if not isinstance(obj, dict):
        return "", {}
    if obj.get("object") == "subscription":
        sub_id = obj.get("id")
    else:
        sub_id = obj.get("subscription")
    if not isinstance(sub_id, str) or not sub_id:
        return "", {}
    return sub_id, obj


def _apply(state: SubscriptionState, event: dict, obj: dict) -> None:
    created = int(event["created"])
    if state.events_applied == 0:
        state.first_event_at = created
    state.events_applied += 1
    state.last_event_at = max(state.last_event_at, created)

    if event["type"] in INVOICE_EVENTS:
        state.last_invoice_id = obj.get("id", state.last_invoice_id)
        state.last_invoice_status = obj.get("status", state.last_invoice_status)
        if event["type"] == "invoice.payment_succeeded":
            state.invoices_paid += 1
        else:
            state.invoices_failed += 1
        return

    # A subscription event carries the object as it is after the change, so
    # every field is taken rather than adjusted. Nothing here computes a new
    # status from an old one, which means a replayed event is harmless.
    state.customer_id = obj.get("customer", state.customer_id)
    state.status = obj.get("status", state.status)
    state.current_period_start = obj.get("current_period_start", state.current_period_start)
    state.current_period_end = obj.get("current_period_end", state.current_period_end)
    state.cancel_at_period_end = bool(
        obj.get("cancel_at_period_end", state.cancel_at_period_end)
    )

    items = (obj.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        state.plan = price.get("id", state.plan)
        state.quantity = items[0].get("quantity", state.quantity)


def reconstruct(events, key=event_order_key) -> Reconstruction:
    """Fold a stream of envelopes into per subscription state.

    Pass `key=None` to apply the events in the order they were handed over.
    That arm exists to measure what ordering by arrival costs, and it is not
    how this platform reads its own log.
    """
    out = Reconstruction(delivered=len(events))

    seen: set[str] = set()
    unique = []
    for event in events:
        event_id = event.get("id")
        if event_id in seen:
            out.duplicates_dropped += 1
            continue
        seen.add(event_id)
        unique.append(event)

    ordered = sorted(unique, key=key) if key is not None else unique
    ambiguous = ambiguous_seconds(unique)

    for event in ordered:
        sub_id, obj = subject_of(event)
        if not sub_id:
            out.skipped_no_object += 1
            continue
        if sub_id not in out.subscriptions:
            if event["type"] != "customer.subscription.created":
                # Reached a subscription before its creation event. Impossible
                # under the event time key and routine under arrival order, so
                # the count is the measurement rather than an error path.
                out.orphan_events += 1
            out.subscriptions[sub_id] = SubscriptionState(subscription_id=sub_id)
        _apply(out.subscriptions[sub_id], event, obj)
        out.applied += 1

    for sub_id, count in ambiguous.items():
        if sub_id in out.subscriptions:
            out.subscriptions[sub_id].ambiguous_seconds = count
    out.ambiguous_seconds = sum(ambiguous.values())

    return out


def ambiguous_seconds(events) -> dict[str, int]:
    """Count the tied seconds per subscription that the type rank cannot settle.

    A second holding two events of the same rank has no order anywhere in the
    data. The sort still produces one, off the event id, and an id is random.
    So the ordering is deterministic and the answer it picks is arbitrary, and
    those are two different properties. Counting the cases is the only honest
    thing to do with them, because a consumer reading a field that a coin flip
    decided should be told which field that was.
    """
    groups: dict[tuple, list] = {}
    for event in events:
        sub_id, _ = subject_of(event)
        if not sub_id:
            continue
        groups.setdefault((sub_id, int(event["created"])), []).append(event)

    counts: dict[str, int] = {}
    for (sub_id, _), group in groups.items():
        if len(group) < 2:
            continue
        ranks = [TYPE_RANK.get(e["type"], UNRANKED) for e in group]
        if len(set(ranks)) < len(ranks):
            counts[sub_id] = counts.get(sub_id, 0) + 1
    return counts


def state_digest(states: dict[str, SubscriptionState]) -> str:
    """One hash over every field of every subscription, ordered by id."""
    blob = json.dumps(
        [asdict(states[k]) for k in sorted(states)],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def differences(left: dict, right: dict) -> list[str]:
    """Subscription ids whose reconstructed state is not identical in both."""
    ids = sorted(set(left) | set(right))
    return [i for i in ids if left.get(i) != right.get(i)]


def tie_resolution(events) -> dict:
    """How the tied seconds actually get resolved, counted rather than assumed.

    A tie is two or more events for one subscription inside one second. The
    type rank settles a tie when the events in it have different ranks. When
    they do not, the ordering falls through to the event id, and that is the
    step with no meaning behind it. The count of those is the honest measure of
    how far the rule reaches.
    """
    buckets: dict[tuple, list] = {}
    for event in events:
        sub_id, _ = subject_of(event)
        if not sub_id:
            continue
        buckets.setdefault((sub_id, int(event["created"])), []).append(event)

    tied = [group for group in buckets.values() if len(group) > 1]
    by_rank = 0
    by_id = 0
    for group in tied:
        ranks = {TYPE_RANK.get(e["type"], UNRANKED) for e in group}
        if len(ranks) == len(group):
            by_rank += 1
        else:
            by_id += 1

    return {
        "tied_seconds": len(tied),
        "events_in_ties": sum(len(g) for g in tied),
        "resolved_by_type_rank": by_rank,
        "resolved_by_event_id": by_id,
    }
