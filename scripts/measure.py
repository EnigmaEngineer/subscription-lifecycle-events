"""Derive every figure this repository publishes and write it to measurements/.

Nothing in the README is typed. A figure that appears there comes out of this
script, and tests/test_readme.py reads the manifest back and refuses a README
number that no longer matches. The point is that a stale figure fails a test
rather than sitting in a document nobody re-derives.

    python scripts/measure.py
"""

from __future__ import annotations

import base64
import glob
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from subevents import config  # noqa: E402
from subevents import generator as gen  # noqa: E402
from subevents.store import EventStore  # noqa: E402
from subevents.webhook import WebhookHandler  # noqa: E402

import replay  # noqa: E402

LIVE_SECRET, ROTATED_SECRET = config.FIXTURE_SECRETS

MEASURED_ON = "2026-11-19"


def fixture_figures() -> dict:
    """Run each fixture against its own empty endpoint, then against one shared one.

    The two numbers are different and the difference is the point. A fixture's
    expectation describes one request arriving at an endpoint that has not seen
    it, so grading it needs a fresh store. The first version of this graded all
    23 against one shared store and reported 19 matches, and the four misses
    were the four valid cases carrying the same event id. Nothing was wrong with
    the handler. The harness was asking a different question than the fixtures
    answer.

    The shared pass is kept because it demonstrates the actual claim. Four valid
    deliveries of one event id, three of them under different signature headers,
    leave one row in the event log. The header is not part of the identity.
    """
    paths = sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json")))
    outcomes: Counter[str] = Counter()
    statuses: Counter[int] = Counter()
    agreed = 0

    for path in paths:
        fixture = json.load(open(path, encoding="utf-8"))
        body = base64.b64decode(fixture["body_b64"])
        store = EventStore()
        handler = WebhookHandler(store, [LIVE_SECRET, ROTATED_SECRET])
        response = handler.handle(body, fixture["header"], fixture["now"])
        store.close()
        outcomes[response.outcome] += 1
        statuses[response.status] += 1
        if (
            response.status == fixture["expect"]["status"]
            and response.outcome == fixture["expect"]["outcome"]
        ):
            agreed += 1

    shared_store = EventStore()
    shared_handler = WebhookHandler(shared_store, [LIVE_SECRET, ROTATED_SECRET])
    shared: Counter[str] = Counter()
    for path in paths:
        fixture = json.load(open(path, encoding="utf-8"))
        body = base64.b64decode(fixture["body_b64"])
        response = shared_handler.handle(body, fixture["header"], fixture["now"])
        shared[response.outcome] += 1
    shared_figures = {
        "accepted": shared["accepted"],
        "duplicate": shared["duplicate"],
        "stored_events": shared_store.event_count(),
    }
    shared_store.close()

    return {
        "fixtures": len(paths),
        "handler_matches_expectation": agreed,
        "outcomes": dict(sorted(outcomes.items())),
        "statuses": {str(k): v for k, v in sorted(statuses.items())},
        "shared_endpoint": shared_figures,
    }


def replay_figures() -> dict:
    """Deliver the whole wire stream through the handler and count decisions."""
    clean = gen.generate()
    wire = gen.to_wire(clean)

    from subevents import signature

    store = EventStore()
    handler = WebhookHandler(store, LIVE_SECRET)
    decisions: Counter[str] = Counter()

    for event in wire:
        body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
        stamp = event["created"]
        header = signature.sign(body, LIVE_SECRET, stamp)
        response = handler.handle(body, header, stamp + 1)
        decisions[response.outcome] += 1

    figures = {
        "clean_events": len(clean),
        "wire_deliveries": len(wire),
        "accepted": decisions["accepted"],
        "duplicate": decisions["duplicate"],
        "conflict": decisions["conflict"],
        "rejected": decisions["rejected"],
        "stored_events": store.event_count(),
        "delivery_total": store.delivery_total(),
    }
    store.close()

    figures["types"] = dict(sorted(Counter(e["type"] for e in clean).items()))
    figures["displacement"] = gen.displacement_stats(clean, wire)
    figures["ties"] = gen.tie_stats(clean)
    return figures


def determinism_figures() -> dict:
    """Two generator runs on one seed have to produce identical bytes."""
    import hashlib

    def digest(events: list[dict]) -> str:
        blob = b"".join(
            json.dumps(e, separators=(",", ":"), sort_keys=True).encode() + b"\n" for e in events
        )
        return hashlib.sha256(blob).hexdigest()

    first = digest(gen.generate())
    second = digest(gen.generate())
    wire_first = digest(gen.to_wire(gen.generate()))
    wire_second = digest(gen.to_wire(gen.generate()))
    return {
        "clean_sha256": first,
        "clean_reproduces": first == second,
        "wire_sha256": wire_first,
        "wire_reproduces": wire_first == wire_second,
    }


def main(argv=None) -> None:
    # The suite runs this script to check the README fence against its stdout.
    # Without somewhere else to write, that run overwrites the committed
    # manifest, and a run under a mutated module leaves a manifest derived from
    # code that is no longer in the tree. A mutation pass found that the hard
    # way on 2026-11-19 when the control came back dirty after the module had
    # already been restored.
    argv = sys.argv[1:] if argv is None else argv
    out_dir = os.path.join(ROOT, "measurements")
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]

    manifest = {
        "generated_by": "scripts/measure.py",
        "python": sys.version.split()[0],
        "measured_on": MEASURED_ON,
        "fixtures": fixture_figures(),
        "replay": replay_figures(),
        "determinism": determinism_figures(),
        "reconstruction": replay.equivalence_figures(),
        "ambiguity": replay.ambiguity_figures(),
    }

    os.makedirs(out_dir, exist_ok=True)
    body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    # The dated file is the history. latest.json is what the README gate reads,
    # so nobody has to remember to repoint a test on the morning of a re-measure.
    for name in (MEASURED_ON + ".json", "latest.json"):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            handle.write(body)

    rep = manifest["replay"]
    fix = manifest["fixtures"]
    # Printed in the shape the README publishes, so the fence in the README is
    # this output and not a retyping of it.
    print(f"fixtures                  {fix['fixtures']}")
    print(f"handler matches fixture   {fix['handler_matches_expectation']}")
    print(f"clean events              {rep['clean_events']}")
    print(f"wire deliveries           {rep['wire_deliveries']}")
    print(f"accepted                  {rep['accepted']}")
    print(f"duplicate                 {rep['duplicate']}")
    print(f"stored events             {rep['stored_events']}")
    print(f"rank max displacement     {rep['displacement']['rank_max_displacement']}")
    print(f"raw max displacement      {rep['displacement']['raw_max_displacement']}")
    print(f"tied seconds              {rep['ties']['tied_seconds']}")
    print(f"events in ties            {rep['ties']['events_in_ties']}")


if __name__ == "__main__":
    main()
