import json
from collections import Counter

from subevents import generator as gen
from subevents.webhook import HANDLED_TYPES


def blob(events):
    return b"".join(
        json.dumps(e, separators=(",", ":"), sort_keys=True).encode() + b"\n" for e in events
    )


def test_one_seed_gives_one_stream():
    assert blob(gen.generate()) == blob(gen.generate())


def test_a_different_seed_gives_a_different_stream():
    a = gen.generate(gen.GeneratorConfig(seed=1))
    b = gen.generate(gen.GeneratorConfig(seed=2))
    assert blob(a) != blob(b)


def test_wire_reproduces_too():
    clean = gen.generate()
    assert blob(gen.to_wire(clean)) == blob(gen.to_wire(clean))


def test_every_id_is_unique_in_the_clean_stream():
    ids = [e["id"] for e in gen.generate()]
    assert len(ids) == len(set(ids))


def test_clean_stream_is_sorted_by_event_time():
    events = gen.generate()
    stamps = [e["created"] for e in events]
    assert stamps == sorted(stamps)


def test_the_generator_only_emits_types_the_handler_wants():
    types = {e["type"] for e in gen.generate()}
    assert types <= HANDLED_TYPES


def test_every_handled_type_actually_appears():
    # A generator that never emits a type leaves that branch of the handler
    # ungraded by the replay, and the replay is most of the evidence here.
    types = {e["type"] for e in gen.generate()}
    assert types == HANDLED_TYPES


def test_wire_is_the_clean_stream_plus_repeats():
    clean = gen.generate()
    wire = gen.to_wire(clean)
    assert len(wire) > len(clean)
    assert {e["id"] for e in wire} == {e["id"] for e in clean}
    counts = Counter(e["id"] for e in wire)
    assert min(counts.values()) == 1
    assert max(counts.values()) >= 2


def test_wire_actually_reorders():
    clean = gen.generate()
    wire = gen.to_wire(clean)
    stats = gen.displacement_stats(clean, wire)
    assert stats["rank_moved"] > 0
    assert stats["rank_max_displacement"] > 0


def test_rank_displacement_respects_the_configured_bound():
    """The jitter bound is a property of the reordering and not of the duplicates.

    Two events can cross by up to twice the bound, so the ceiling checked here
    is 2n and not n. The raw index figure is deliberately not bounded by this.
    """
    clean = gen.generate()
    for bound in (4, 12, 30):
        wire = gen.to_wire(clean, gen.WireConfig(seed=5, max_displacement=bound))
        stats = gen.displacement_stats(clean, wire)
        assert stats["rank_max_displacement"] <= 2 * bound, bound


def test_raw_displacement_is_inflated_by_the_duplicates():
    clean = gen.generate()
    wire = gen.to_wire(clean)
    stats = gen.displacement_stats(clean, wire)
    assert stats["raw_max_displacement"] > stats["rank_max_displacement"]
    assert stats["raw_max_displacement"] >= stats["duplicates"]


def test_no_duplicates_means_the_two_figures_converge():
    clean = gen.generate()
    wire = gen.to_wire(clean, gen.WireConfig(seed=5, duplicate_rate=0.0, triple_rate=0.0))
    stats = gen.displacement_stats(clean, wire)
    assert stats["duplicates"] == 0
    assert stats["raw_max_displacement"] == stats["rank_max_displacement"]


def test_the_stream_contains_ties_on_purpose():
    stats = gen.tie_stats(gen.generate())
    assert stats["tied_seconds"] > 0
    assert stats["events_in_ties"] >= 2 * stats["tied_seconds"]


def test_a_tie_holds_two_different_events_for_one_subscription():
    events = gen.generate()
    buckets = {}
    for event in events:
        obj = event["data"]["object"]
        key = (obj.get("subscription") or obj.get("id"), event["created"])
        buckets.setdefault(key, []).append(event)
    tied = [v for v in buckets.values() if len(v) > 1]
    assert tied
    first = tied[0]
    assert len({e["id"] for e in first}) == len(first)


def test_population_size_is_honoured():
    events = gen.generate(gen.GeneratorConfig(subscriptions=7))
    created = [e for e in events if e["type"] == "customer.subscription.created"]
    assert len(created) == 7


def test_every_event_carries_the_fields_the_handler_reads():
    for event in gen.generate(gen.GeneratorConfig(subscriptions=15)):
        assert isinstance(event["id"], str) and event["id"].startswith("evt_")
        assert isinstance(event["created"], int)
        assert isinstance(event["type"], str)
        assert "object" in event["data"]
