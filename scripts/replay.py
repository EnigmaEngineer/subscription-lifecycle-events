"""Replay the same events twice and check the state comes out the same.

The clean stream is in event time order with nothing repeated. The wire stream
is the same events shuffled inside a bounded window with duplicates inserted,
which is what a retrying sender behind a queue actually delivers. Both go
through the real handler into a real store, both get read back out of the
append only log, and both get folded into subscription state.

If the two disagree the platform does not have state reconstruction, it has a
function that happens to work on sorted input.

    python scripts/replay.py
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from subevents import config  # noqa: E402
from subevents import generator as gen  # noqa: E402
from subevents import signature  # noqa: E402
from subevents import state  # noqa: E402
from subevents.store import EventStore  # noqa: E402
from subevents.webhook import WebhookHandler  # noqa: E402

LIVE_SECRET = config.FIXTURE_SECRETS[0]


def deliver(events: list[dict]) -> list[dict]:
    """Sign and deliver every event, then read the log back out of the store."""
    store = EventStore()
    handler = WebhookHandler(store, LIVE_SECRET)
    for event in events:
        body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
        stamp = int(event["created"])
        handler.handle(body, signature.sign(body, LIVE_SECRET, stamp), stamp + 1)
    envelopes = store.stored_envelopes()
    store.close()
    return envelopes


def id_descending_key(event: dict) -> tuple:
    """`event_order_key` with the id tie break running the other way.

    Only the seconds the type rank cannot settle move. Anything that changes
    between this arm and the real one was decided by a sort on a random string
    and is not a property of the subscription.
    """
    return (
        int(event["created"]),
        state.TYPE_RANK.get(event["type"], state.UNRANKED),
        tuple(-ord(c) for c in event["id"]),
    )


def equivalence_figures() -> dict:
    clean = gen.generate()
    wire = gen.to_wire(clean)

    clean_log = deliver(clean)
    wire_log = deliver(wire)

    from_clean = state.reconstruct(clean_log)
    from_wire = state.reconstruct(wire_log)
    # Straight off the wire with no store in the way. The store already refuses
    # a repeat, so a reconstruction read out of the log never meets a duplicate
    # and its own dedupe is untested by that route.
    from_stream = state.reconstruct(wire)
    by_arrival = state.reconstruct(wire_log, key=None)
    ties_reversed = state.reconstruct(clean_log, key=state.reversed_tie_key)

    resolution = state.tie_resolution(clean)

    return {
        "clean_deliveries": len(clean),
        "wire_deliveries": len(wire),
        "clean_log_rows": len(clean_log),
        "wire_log_rows": len(wire_log),
        "subscriptions": len(from_clean.subscriptions),
        "clean_state_sha256": from_clean.digest(),
        "wire_state_sha256": from_wire.digest(),
        "replays_agree": from_clean.digest() == from_wire.digest(),
        "duplicates_refused_by_the_store": len(wire) - len(wire_log),
        "duplicates_dropped_by_the_fold": from_stream.duplicates_dropped,
        "stream_replay_agrees": from_clean.digest() == from_stream.digest(),
        "tied_seconds": resolution["tied_seconds"],
        "resolved_by_type_rank": resolution["resolved_by_type_rank"],
        "resolved_by_event_id": resolution["resolved_by_event_id"],
        "subscriptions_changed_by_tie_order": len(
            state.differences(from_clean.subscriptions, ties_reversed.subscriptions)
        ),
        "arrival_order_subscriptions_wrong": len(
            state.differences(from_clean.subscriptions, by_arrival.subscriptions)
        ),
        "arrival_order_orphan_events": by_arrival.orphan_events,
    }


def ambiguity_figures() -> dict:
    """The arm that exercises the tie the ordering rule cannot settle.

    The published corpus has no second holding two events of the same rank, so
    the id tie break never decides anything in it and the rule reads as proven
    when it is only unexercised. This config plants that case. It is off by
    default because switching it on changes every byte the generator produces.
    """
    cfg = gen.GeneratorConfig(same_second_update_rate=0.35)
    clean = gen.generate(cfg)
    wire = gen.to_wire(clean)

    primary = state.reconstruct(clean)
    shuffled = state.reconstruct(wire)
    flipped = state.reconstruct(clean, key=id_descending_key)

    changed = set(state.differences(primary.subscriptions, flipped.subscriptions))
    flagged = {k for k, v in primary.subscriptions.items() if v.ambiguous_seconds}
    resolution = state.tie_resolution(clean)

    return {
        "probe_events": len(clean),
        "probe_replays_agree": primary.digest() == shuffled.digest(),
        "probe_tied_seconds": resolution["tied_seconds"],
        "probe_resolved_by_event_id": resolution["resolved_by_event_id"],
        "probe_ambiguous_seconds": primary.ambiguous_seconds,
        "probe_subscriptions_flagged": len(flagged),
        "probe_subscriptions_changed_by_id_order": len(changed),
        "probe_changed_but_not_flagged": len(changed - flagged),
    }


def main() -> None:
    eq = equivalence_figures()
    amb = ambiguity_figures()
    # Printed in the shape the README publishes it, so the fence in the
    # document is this output rather than a retyping of it.
    print(f"subscriptions             {eq['subscriptions']}")
    print(f"clean log rows            {eq['clean_log_rows']}")
    print(f"wire deliveries           {eq['wire_deliveries']}")
    print(f"wire log rows             {eq['wire_log_rows']}")
    print(f"replays agree             {'yes' if eq['replays_agree'] else 'NO'}")
    print(f"replay straight off wire  {'yes' if eq['stream_replay_agrees'] else 'NO'}")
    print(f"tied seconds              {eq['tied_seconds']}")
    print(f"resolved by type rank     {eq['resolved_by_type_rank']}")
    print(f"resolved by event id      {eq['resolved_by_event_id']}")
    print(f"tie order changes state   {eq['subscriptions_changed_by_tie_order']}")
    print(f"arrival order wrong subs  {eq['arrival_order_subscriptions_wrong']}")
    print(f"arrival order orphans     {eq['arrival_order_orphan_events']}")
    print("")
    print(f"probe tied seconds        {amb['probe_tied_seconds']}")
    print(f"probe ties left to the id {amb['probe_resolved_by_event_id']}")
    print(f"probe subs flagged        {amb['probe_subscriptions_flagged']}")
    print(f"probe subs id order moved {amb['probe_subscriptions_changed_by_id_order']}")
    print(f"probe moved but unflagged {amb['probe_changed_but_not_flagged']}")


if __name__ == "__main__":
    main()
