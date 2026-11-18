"""Verification of Stripe style webhook signatures.

The header Stripe sends looks like this.

    Stripe-Signature: t=1700000000,v1=5257a8...,v1=9f2b1c...

The signed payload is the timestamp, a full stop, then the raw request body
bytes. Two v1 values appear while a secret is being rotated, so both have to be
tried. Nothing here parses JSON. A verifier that reads the body before it has
checked the signature is doing work on behalf of whoever sent it.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

DEFAULT_TOLERANCE_SECONDS = 300

SCHEME = "v1"


class SignatureError(Exception):
    """Raised when a signature header cannot be trusted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParsedHeader:
    timestamp: int
    signatures: tuple[str, ...]


def parse_header(header: str) -> ParsedHeader:
    """Pull the timestamp and every v1 value out of a signature header.

    Unknown keys are ignored rather than refused. Stripe has added schemes
    before and a verifier that refuses anything it does not recognise breaks on
    the day they add another one.
    """
    if not header or not header.strip():
        raise SignatureError("empty signature header")

    timestamp: int | None = None
    signatures: list[str] = []

    for part in header.split(","):
        if "=" not in part:
            # A bare token is not a key value pair. Stripe does not send one.
            raise SignatureError("malformed element in signature header")
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "t":
            if timestamp is not None:
                raise SignatureError("more than one timestamp in signature header")
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureError("timestamp is not an integer") from exc
        elif key == SCHEME:
            if value:
                signatures.append(value)

    if timestamp is None:
        raise SignatureError("no timestamp in signature header")
    if not signatures:
        raise SignatureError("no v1 signature in signature header")

    return ParsedHeader(timestamp=timestamp, signatures=tuple(signatures))


def expected_signature(payload: bytes, timestamp: int, secret: str) -> str:
    """Return the hex digest Stripe would have sent for this payload."""
    signed = str(timestamp).encode("ascii") + b"." + payload
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify(
    payload: bytes,
    header: str,
    secrets: list[str] | tuple[str, ...] | str,
    now: float | None = None,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> int:
    """Check a signature header against a body and return its timestamp.

    Raises SignatureError on anything that does not verify. The timestamp comes
    back because the caller wants it for the received_at column and should not
    have to parse the header a second time to get it.
    """
    if isinstance(secrets, str):
        secrets = (secrets,)
    if not secrets:
        raise SignatureError("no signing secret configured")

    parsed = parse_header(header)
    current = time.time() if now is None else now

    # Replay window. Stripe's own default is five minutes and a longer one here
    # would be a weaker check than the sender assumes.
    age = current - parsed.timestamp
    if age > tolerance:
        raise SignatureError("timestamp outside tolerance")
    if age < -tolerance:
        raise SignatureError("timestamp is in the future")

    for secret in secrets:
        candidate = expected_signature(payload, parsed.timestamp, secret)
        for supplied in parsed.signatures:
            # compare_digest and not == because == returns on the first wrong
            # byte and that difference is measurable over enough requests.
            if hmac.compare_digest(candidate, supplied):
                return parsed.timestamp

    raise SignatureError("no signature matched")


def sign(payload: bytes, secret: str, timestamp: int, extra_secret: str | None = None) -> str:
    """Build a signature header. Used by the fixture builder and the tests.

    This is the only place in the repository that produces a signature, so a
    test that signs with it and verifies with verify() is testing agreement
    between two functions rather than correctness against Stripe. The fixtures
    carry vectors checked against the documented algorithm by hand.
    """
    values = [f"t={timestamp}", f"v1={expected_signature(payload, timestamp, secret)}"]
    if extra_secret is not None:
        values.append(f"v1={expected_signature(payload, timestamp, extra_secret)}")
    return ",".join(values)
