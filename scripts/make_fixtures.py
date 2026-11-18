"""Write the signed fixture corpus to fixtures/signed/.

One file per case. Each file carries the exact bytes, the exact header and the
status and outcome the handler is expected to return, so a test reads the
expectation out of the fixture rather than restating it.

The bytes are stored base64 encoded. Two cases hold a body that is not valid
utf-8 and a json fixture file cannot carry those any other way.
"""

from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subevents import config, signature

SECRET, ROTATED = config.FIXTURE_SECRETS
WRONG = config.FIXTURE_WRONG_SECRET

NOW = 1763000000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "signed")


def envelope(event_id: str, event_type: str, created: int, obj: dict | None = None) -> bytes:
    body = {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": created,
        "type": event_type,
        "livemode": False,
        "pending_webhooks": 1,
        "request": {"id": None, "idempotency_key": None},
        "data": {"object": obj if obj is not None else {"id": "sub_fixture", "object": "subscription"}},
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def case(name: str, body: bytes, header: str, status: int, outcome: str, note: str) -> dict:
    return {
        "name": name,
        "note": note,
        "now": NOW,
        "header": header,
        "body_b64": base64.b64encode(body).decode("ascii"),
        "expect": {"status": status, "outcome": outcome},
    }


def build() -> list[dict]:
    good = envelope("evt_fixture_good", "customer.subscription.created", NOW - 5)
    cases = []

    cases.append(
        case(
            "valid-single-secret",
            good,
            signature.sign(good, SECRET, NOW - 5),
            200,
            "accepted",
            "the ordinary case, one v1 value signed with the live secret",
        )
    )

    cases.append(
        case(
            "valid-during-rotation",
            good,
            signature.sign(good, ROTATED, NOW - 5, extra_secret=SECRET),
            200,
            "accepted",
            "two v1 values. the first is the retired secret and the second is live",
        )
    )

    cases.append(
        case(
            "valid-rotated-secret-first-in-list",
            good,
            signature.sign(good, SECRET, NOW - 5, extra_secret=ROTATED),
            200,
            "accepted",
            "same as above with the order swapped, so neither position is privileged",
        )
    )

    cases.append(
        case(
            "expired-timestamp",
            good,
            signature.sign(good, SECRET, NOW - 400),
            400,
            "rejected",
            "signature is correct and the timestamp is older than the five minute window",
        )
    )

    cases.append(
        case(
            "timestamp-in-the-future",
            good,
            signature.sign(good, SECRET, NOW + 400),
            400,
            "rejected",
            "a clock ahead of ours by more than the tolerance is refused in both directions",
        )
    )

    cases.append(
        case(
            "tolerance-boundary-inside",
            good,
            signature.sign(good, SECRET, NOW - 300),
            200,
            "accepted",
            "exactly at the tolerance. the comparison is inclusive on purpose",
        )
    )

    cases.append(
        case(
            "tolerance-boundary-outside",
            good,
            signature.sign(good, SECRET, NOW - 301),
            400,
            "rejected",
            "one second past the tolerance, which is the other half of the boundary",
        )
    )

    cases.append(
        case(
            "wrong-secret",
            good,
            signature.sign(good, WRONG, NOW - 5),
            400,
            "rejected",
            "well formed header signed with a secret this endpoint was never given",
        )
    )

    mutated = bytearray(good)
    pos = mutated.index(b"customer.subscription.created")
    mutated[pos] = ord("C")
    cases.append(
        case(
            "body-mutated-one-byte",
            bytes(mutated),
            signature.sign(good, SECRET, NOW - 5),
            400,
            "rejected",
            "header signs the original body and one byte of the body changed in transit",
        )
    )

    cases.append(
        case(
            "missing-timestamp",
            good,
            "v1=" + signature.expected_signature(good, NOW - 5, SECRET),
            400,
            "rejected",
            "no t element, so there is nothing to sign over and nothing to age check",
        )
    )

    cases.append(
        case("missing-v1", good, f"t={NOW - 5}", 400, "rejected", "a timestamp and no signature at all")
    )

    cases.append(
        case(
            "timestamp-not-an-integer",
            good,
            f"t=yesterday,v1=" + signature.expected_signature(good, NOW - 5, SECRET),
            400,
            "rejected",
            "t has to parse as an integer before anything else happens",
        )
    )

    cases.append(case("empty-header", good, "", 400, "rejected", "no header on the request"))

    cases.append(
        case(
            "bare-token-in-header",
            good,
            f"t={NOW - 5},garbage,v1=" + signature.expected_signature(good, NOW - 5, SECRET),
            400,
            "rejected",
            "an element with no equals sign. a real sender does not produce one",
        )
    )

    cases.append(
        case(
            "unknown-scheme-ignored",
            good,
            f"t={NOW - 5},v0=abc123,v1=" + signature.expected_signature(good, NOW - 5, SECRET),
            200,
            "accepted",
            "an unrecognised scheme key is skipped rather than refused",
        )
    )

    cases.append(
        case(
            "uppercase-hex-signature",
            good,
            f"t={NOW - 5},v1=" + signature.expected_signature(good, NOW - 5, SECRET).upper(),
            400,
            "rejected",
            "stripe sends lower case hex. accepting upper case would be more lenient than the sender",
        )
    )

    cases.append(
        case(
            "truncated-signature",
            good,
            f"t={NOW - 5},v1=" + signature.expected_signature(good, NOW - 5, SECRET)[:32],
            400,
            "rejected",
            "a prefix of the right digest. compare_digest refuses it on length",
        )
    )

    cases.append(
        case(
            "signed-but-not-json",
            b"\xff\xfe not json at all",
            signature.sign(b"\xff\xfe not json at all", SECRET, NOW - 5),
            400,
            "unparseable",
            "verifies and then does not decode. the sender is real and the bytes are not usable",
        )
    )

    cases.append(
        case(
            "signed-json-array",
            b"[1,2,3]",
            signature.sign(b"[1,2,3]", SECRET, NOW - 5),
            400,
            "unparseable",
            "valid json that is not an event envelope",
        )
    )

    no_type = json.dumps({"id": "evt_no_type", "created": NOW - 5}, separators=(",", ":")).encode()
    cases.append(
        case(
            "envelope-missing-type",
            no_type,
            signature.sign(no_type, SECRET, NOW - 5),
            400,
            "unparseable",
            "the handler reads four fields and this one is absent",
        )
    )

    created_str = json.dumps(
        {"id": "evt_created_str", "type": "customer.subscription.created", "created": "1763000000"},
        separators=(",", ":"),
    ).encode()
    cases.append(
        case(
            "created-as-string",
            created_str,
            signature.sign(created_str, SECRET, NOW - 5),
            400,
            "unparseable",
            "a timestamp sent as a string would sort as text later on",
        )
    )

    created_bool = json.dumps(
        {"id": "evt_created_bool", "type": "customer.subscription.created", "created": True},
        separators=(",", ":"),
    ).encode()
    cases.append(
        case(
            "created-as-boolean",
            created_bool,
            signature.sign(created_bool, SECRET, NOW - 5),
            400,
            "unparseable",
            "True is an int in python and the shape check says so explicitly",
        )
    )

    unhandled = envelope("evt_unhandled", "charge.refunded", NOW - 5)
    cases.append(
        case(
            "unhandled-event-type",
            unhandled,
            signature.sign(unhandled, SECRET, NOW - 5),
            200,
            "ignored",
            "signed and real and not a subscription event. acknowledged so it stops being retried",
        )
    )

    return cases


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for stale in os.listdir(OUT):
        if stale.endswith(".json"):
            os.remove(os.path.join(OUT, stale))

    cases = build()
    for item in cases:
        path = os.path.join(OUT, item["name"] + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(item, handle, indent=2, sort_keys=True)
            handle.write("\n")

    print(f"wrote {len(cases)} fixtures to fixtures/signed/")
    by_outcome: dict[str, int] = {}
    for item in cases:
        key = item["expect"]["outcome"]
        by_outcome[key] = by_outcome.get(key, 0) + 1
    for key in sorted(by_outcome):
        print(f"  {key}: {by_outcome[key]}")


if __name__ == "__main__":
    main()
