"""Product event generator for subscription lifecycles.

Produces Stripe shaped events for a population of subscriptions, in event time
order, and a second view of the same events as they would arrive on the wire.
The wire view is shuffled within a bounded window and carries duplicates,
because that is what a retrying sender behind a queue actually delivers.

Everything is seeded. The same seed gives the same bytes, which is the only
reason a shuffled replay can be compared against a clean one at all.

One thing is deliberate and it is the hardest part of this dataset. Events for
the same subscription are allowed to share a `created` second. Stripe has no
version counter on a subscription object, so a second holding two state changes
has no total order in the data itself. Resolving that is the next piece of work
and the generator is where the problem gets planted rather than hidden.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

ID_ALPHABET = string.ascii_letters + string.digits

PLANS = {
    "price_starter": 1900,
    "price_team": 4900,
    "price_business": 14900,
}

PLAN_ORDER = ("price_starter", "price_team", "price_business")

# A month of seconds. Stripe uses real calendar periods and this does not, which
# is fine for ordering work and wrong for any revenue figure. Nothing in this
# repository reports revenue.
PERIOD = 30 * 24 * 3600


@dataclass
class GeneratorConfig:
    subscriptions: int = 200
    seed: int = 20261118
    start: int = 1760000000
    churn_rate: float = 0.18
    pause_rate: float = 0.12
    upgrade_rate: float = 0.25
    payment_failure_rate: float = 0.08
    periods: int = 4
    # Two updates inside one second for the same subscription. Off by default,
    # because switching it on changes every byte the generator produces and the
    # figures published on 2026-11-18 were measured without it. It exists so
    # the one tie shape the ordering rule cannot resolve can be measured on
    # purpose rather than waited for. See scripts/replay.py.
    same_second_update_rate: float = 0.0


@dataclass
class WireConfig:
    seed: int = 77
    duplicate_rate: float = 0.07
    max_displacement: int = 12
    triple_rate: float = 0.01


@dataclass
class Subscription:
    sub_id: str
    customer_id: str
    plan: str
    created: int
    events: list[dict] = field(default_factory=list)


def _token(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(ID_ALPHABET) for _ in range(n))


def _envelope(rng: random.Random, event_type: str, created: int, obj: dict) -> dict:
    return {
        "id": "evt_" + _token(rng, 24),
        "object": "event",
        "api_version": "2024-06-20",
        "created": created,
        "type": event_type,
        "livemode": False,
        "pending_webhooks": 1,
        "request": {"id": None, "idempotency_key": None},
        "data": {"object": obj},
    }


def _subscription_object(sub: Subscription, status: str, period_start: int, quantity: int) -> dict:
    return {
        "id": sub.sub_id,
        "object": "subscription",
        "customer": sub.customer_id,
        "status": status,
        "created": sub.created,
        "current_period_start": period_start,
        "current_period_end": period_start + PERIOD,
        "cancel_at_period_end": False,
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_" + sub.sub_id[4:16],
                    "object": "subscription_item",
                    "price": {"id": sub.plan, "unit_amount": PLANS[sub.plan]},
                    "quantity": quantity,
                }
            ],
        },
    }


def _invoice_object(sub: Subscription, period_start: int, quantity: int, paid: bool) -> dict:
    amount = PLANS[sub.plan] * quantity
    return {
        "id": "in_" + sub.sub_id[4:20],
        "object": "invoice",
        "customer": sub.customer_id,
        "subscription": sub.sub_id,
        "status": "paid" if paid else "open",
        "amount_due": amount,
        "amount_paid": amount if paid else 0,
        "period_start": period_start,
        "period_end": period_start + PERIOD,
    }


def generate(config: GeneratorConfig | None = None) -> list[dict]:
    """Return every event for the population, sorted by event time."""
    cfg = config or GeneratorConfig()
    rng = random.Random(cfg.seed)
    out: list[dict] = []

    for i in range(cfg.subscriptions):
        sub = Subscription(
            sub_id="sub_" + _token(rng, 24),
            customer_id="cus_" + _token(rng, 24),
            plan=rng.choice(PLAN_ORDER),
            created=cfg.start + rng.randrange(0, PERIOD),
        )
        quantity = rng.choice([1, 1, 1, 2, 3, 5])
        period_start = sub.created
        status = "active"

        out.append(
            _envelope(
                rng,
                "customer.subscription.created",
                sub.created,
                _subscription_object(sub, status, period_start, quantity),
            )
        )

        for period in range(cfg.periods):
            billed_at = period_start + 2
            paid = rng.random() > cfg.payment_failure_rate
            out.append(
                _envelope(
                    rng,
                    "invoice.payment_succeeded" if paid else "invoice.payment_failed",
                    billed_at,
                    _invoice_object(sub, period_start, quantity, paid),
                )
            )

            if not paid:
                # Stripe moves the subscription to past_due in the same second
                # it reports the failed invoice. This is one of the planted ties.
                status = "past_due"
                out.append(
                    _envelope(
                        rng,
                        "customer.subscription.updated",
                        billed_at,
                        _subscription_object(sub, status, period_start, quantity),
                    )
                )

            if rng.random() < cfg.upgrade_rate:
                idx = PLAN_ORDER.index(sub.plan)
                if idx < len(PLAN_ORDER) - 1:
                    sub.plan = PLAN_ORDER[idx + 1]
                else:
                    quantity += 1
                status = "active"
                out.append(
                    _envelope(
                        rng,
                        "customer.subscription.updated",
                        period_start + rng.randrange(3, PERIOD // 2),
                        _subscription_object(sub, status, period_start, quantity),
                    )
                )

            # Guarded by the rate rather than by the draw, so the default
            # config consumes no randomness here and reproduces the corpus the
            # README already publishes.
            if cfg.same_second_update_rate and rng.random() < cfg.same_second_update_rate:
                quantity += 1
                out.append(
                    _envelope(
                        rng,
                        "customer.subscription.updated",
                        billed_at,
                        _subscription_object(sub, status, period_start, quantity),
                    )
                )

            if rng.random() < cfg.pause_rate:
                paused_at = period_start + rng.randrange(PERIOD // 2, PERIOD - 10)
                out.append(
                    _envelope(
                        rng,
                        "customer.subscription.paused",
                        paused_at,
                        _subscription_object(sub, "paused", period_start, quantity),
                    )
                )
                out.append(
                    _envelope(
                        rng,
                        "customer.subscription.resumed",
                        paused_at + rng.randrange(1, 9),
                        _subscription_object(sub, "active", period_start, quantity),
                    )
                )
                status = "active"

            period_start += PERIOD

        if rng.random() < cfg.churn_rate:
            out.append(
                _envelope(
                    rng,
                    "customer.subscription.deleted",
                    period_start + rng.randrange(1, 600),
                    _subscription_object(sub, "canceled", period_start, quantity),
                )
            )

    out.sort(key=lambda e: (e["created"], e["id"]))
    return out


def to_wire(events: list[dict], config: WireConfig | None = None) -> list[dict]:
    """Reorder and duplicate a clean stream into what arrives at the endpoint.

    Displacement is bounded rather than a full shuffle. A sender behind a queue
    with a handful of partitions reorders locally. It does not deliver month two
    before month one.
    """
    cfg = config or WireConfig()
    rng = random.Random(cfg.seed)

    indexed = list(enumerate(events))
    # Jitter the sort key by a bounded amount. Equal jittered keys fall back to
    # the original position, so the result is stable for a given seed.
    jittered = sorted(
        indexed,
        key=lambda pair: (pair[0] + rng.randrange(-cfg.max_displacement, cfg.max_displacement + 1), pair[0]),
    )

    wire: list[dict] = []
    for _, event in jittered:
        wire.append(event)
        roll = rng.random()
        if roll < cfg.triple_rate:
            wire.append(event)
            wire.append(event)
        elif roll < cfg.duplicate_rate:
            wire.append(event)
    return wire


def displacement_stats(clean: list[dict], wire: list[dict]) -> dict:
    """How far events moved, measured two ways because they disagree.

    The first version of this compared an event's clean position against its raw
    index in the wire list and reported a maximum of 135 against a configured
    jitter bound of 12. That figure was almost entirely the duplicates. Every
    copy inserted into the stream pushes all 1,400 events after it along by one,
    so the last event in a stream carrying 126 duplicates reads as displaced by
    126 without having moved at all.

    `rank_*` compares first arrivals against first arrivals and is the figure
    that describes the reordering. `raw_*` keeps the index view, because a
    consumer reading the wire really does see events that far from where they
    belong and the number is true about the stream even though it is useless as
    a measure of shuffling.
    """
    position = {e["id"]: i for i, e in enumerate(clean)}
    seen: set[str] = set()
    rank_shifts = []
    raw_shifts = []
    for index, event in enumerate(wire):
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        clean_position = position[event["id"]]
        rank_shifts.append(abs(len(rank_shifts) - clean_position))
        raw_shifts.append(abs(index - clean_position))

    moved = [s for s in rank_shifts if s > 0]
    return {
        "events": len(rank_shifts),
        "duplicates": len(wire) - len(rank_shifts),
        "rank_moved": len(moved),
        "rank_max_displacement": max(rank_shifts) if rank_shifts else 0,
        "rank_mean_displacement_x1000": (
            round(1000 * sum(rank_shifts) / len(rank_shifts)) if rank_shifts else 0
        ),
        "raw_max_displacement": max(raw_shifts) if raw_shifts else 0,
    }


def tie_stats(events: list[dict]) -> dict:
    """Count seconds holding more than one event for the same subscription."""
    buckets: dict[tuple[str, int], int] = {}
    for event in events:
        obj = event["data"]["object"]
        sub_id = obj.get("subscription") or obj.get("id")
        key = (sub_id, event["created"])
        buckets[key] = buckets.get(key, 0) + 1
    tied = {k: v for k, v in buckets.items() if v > 1}
    return {
        "subscription_seconds": len(buckets),
        "tied_seconds": len(tied),
        "events_in_ties": sum(tied.values()),
        "widest_tie": max(tied.values()) if tied else 0,
    }
