"""Write a generated event stream to disk as newline delimited json.

    python scripts/gen_events.py --subscriptions 200 --out data/

Two files come out. `clean.ndjson` is event time order. `wire.ndjson` is the
same events reordered and duplicated the way the endpoint receives them. The
clean file is the answer key for the replay work.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subevents.generator import GeneratorConfig, WireConfig, generate, to_wire


def write_ndjson(path: str, events: list[dict]) -> int:
    written = 0
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
            handle.write("\n")
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscriptions", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--wire-seed", type=int, default=77)
    parser.add_argument("--out", default="data")
    args = parser.parse_args()

    clean = generate(GeneratorConfig(subscriptions=args.subscriptions, seed=args.seed))
    wire = to_wire(clean, WireConfig(seed=args.wire_seed))

    os.makedirs(args.out, exist_ok=True)
    n_clean = write_ndjson(os.path.join(args.out, "clean.ndjson"), clean)
    n_wire = write_ndjson(os.path.join(args.out, "wire.ndjson"), wire)

    print(f"clean.ndjson {n_clean} events")
    print(f"wire.ndjson  {n_wire} deliveries")


if __name__ == "__main__":
    main()
