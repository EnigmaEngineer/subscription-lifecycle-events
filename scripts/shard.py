"""What the idempotency ledger costs when it stops being one ledger.

The store written on 2026-11-18 holds the dedupe ledger and the event log in
one sqlite file and writes both in one transaction. The reasoning for that is
sound and it assumes there is one of them.

Behind API Gateway the handler is a Lambda function. Each concurrent execution
environment gets its own /tmp, so each one gets its own ledger, and a retry
that lands anywhere else meets a ledger that has never seen the event. The
guarantee is not weakened a little. It is divided.

Two dispatch arms, because the answer depends entirely on which one you assume.

    round robin   every delivery goes to the next environment in turn
    random        every delivery goes to an environment drawn at random

Round robin is the adversarial arm rather than the gentle one. This generator
inserts every duplicate immediately after its original, so taking environments
in turn guarantees that a pair of adjacent deliveries never meets itself.
Random dispatch is the better model of a real invocation and it should refuse
roughly one in k.

    python scripts/shard.py
"""

from __future__ import annotations

import json
import os
import random
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

SHARD_COUNTS = (1, 2, 4, 8, 16)

# Fixed so the random arm reproduces. A seed chosen once and written down is a
# measurement. A seed left to the clock is an anecdote.
DISPATCH_SEED = 20261120


def round_robin(count: int):
    return lambda index: index % count


def random_dispatch(count: int, seed: int = DISPATCH_SEED):
    rng = random.Random(seed)
    return lambda index: rng.randrange(count)


def deliver_sharded(events: list[dict], shards: int, assign) -> dict:
    """Deliver the whole stream across `shards` independent stores.

    The union of the logs is what a consumer reading every environment would
    see, which is the number that matters. A row the ledger failed to refuse is
    a row the downstream has to handle.
    """
    stores = [EventStore() for _ in range(shards)]
    handlers = [WebhookHandler(store, LIVE_SECRET) for store in stores]
    choose = assign(shards)

    accepted = 0
    duplicates = 0
    for index, event in enumerate(events):
        body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
        stamp = int(event["created"])
        response = handlers[choose(index)].handle(
            body, signature.sign(body, LIVE_SECRET, stamp), stamp + 1
        )
        if response.outcome == "accepted":
            accepted += 1
        elif response.outcome == "duplicate":
            duplicates += 1

    union: list[dict] = []
    for store in stores:
        union.extend(store.stored_envelopes())
        store.close()

    distinct = len({envelope["id"] for envelope in union})
    reconstruction = state.reconstruct(union)
    return {
        "shards": shards,
        "accepted": accepted,
        "refused_as_duplicate": duplicates,
        "rows_across_logs": len(union),
        "distinct_event_ids": distinct,
        "rows_beyond_one_per_event": len(union) - distinct,
        "state_sha256": reconstruction.digest(),
        "dropped_by_the_fold": reconstruction.duplicates_dropped,
    }


def figures() -> dict:
    clean = gen.generate()
    wire = gen.to_wire(clean)

    arms = {
        "round_robin": [deliver_sharded(wire, k, round_robin) for k in SHARD_COUNTS],
        "random": [deliver_sharded(wire, k, random_dispatch) for k in SHARD_COUNTS],
    }

    # The question the whole measurement exists to answer. A split ledger
    # changes the row count for certain. Whether it changes the answer depends
    # on something else entirely, which is the dedupe inside the fold.
    digests = {arm["state_sha256"] for group in arms.values() for arm in group}

    return {
        "wire_deliveries": len(wire),
        "distinct_events": len({event["id"] for event in wire}),
        "duplicate_deliveries": len(wire) - len({event["id"] for event in wire}),
        "arms": arms,
        "distinct_state_digests": len(digests),
        "state_survives_every_split": len(digests) == 1,
    }


def main() -> None:
    result = figures()
    print(f"wire deliveries           {result['wire_deliveries']}")
    print(f"distinct events           {result['distinct_events']}")
    print(f"duplicate deliveries      {result['duplicate_deliveries']}")

    for name, group in result["arms"].items():
        print()
        print(f"{name} dispatch")
        print("  ledgers  refused  rows  extra rows  fold dropped")
        for arm in group:
            print(
                f"  {arm['shards']:>7}  {arm['refused_as_duplicate']:>7}"
                f"  {arm['rows_across_logs']:>4}  {arm['rows_beyond_one_per_event']:>10}"
                f"  {arm['dropped_by_the_fold']:>12}"
            )

    print()
    print(f"distinct state digests    {result['distinct_state_digests']}")
    print(
        "state survives every split "
        f"{'yes' if result['state_survives_every_split'] else 'no'}"
    )


if __name__ == "__main__":
    main()
