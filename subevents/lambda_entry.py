"""The handler behind API Gateway, and the two things a gateway does to a body.

The verifier signs over raw request bytes. API Gateway does not hand Lambda raw
request bytes. It hands over a JSON event with the body as a string and a flag
saying whether that string is base64. Payload format 2.0 also lowercases every
header name and joins repeated headers with a comma.

So this module is the only place in the repository that is allowed to decide
what "the body" means. Everything downstream takes bytes. The reason it is a
module and not four lines inside the entry point is that the decoding rule is
the part worth grading, and a function can be called by a test.

What this does not do is produce to Kafka. The publisher is a seam and the
local one writes nowhere. See the limitations section of the README.
"""

from __future__ import annotations

import base64
import json
import os

from subevents import config
from subevents.store import EventStore
from subevents.webhook import WebhookHandler

SIGNATURE_HEADER = "stripe-signature"

# Lambda gets a writable /tmp and nothing else, and the ledger lives in sqlite.
# That pairing is wrong and it is wrong in a way worth measuring rather than
# worth hiding, so the path is configurable and the default is honest about
# where it lands. scripts/shard.py measures what the wrong pairing costs.
LEDGER_PATH_VAR = "SUBEVENTS_LEDGER_PATH"
DEFAULT_LEDGER_PATH = "/tmp/subevents.db"


class GatewayRequestError(Exception):
    """Raised when the gateway event cannot be turned into a signed request."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def signature_header(headers: object) -> str:
    """Find the signature header without trusting its case.

    Payload format 2.0 lowercases header names and payload format 1.0 does not,
    and a local test harness sends whatever it was written to send. Matching on
    one spelling is a bug that only shows up once, in the environment you cannot
    run yet.
    """
    if not isinstance(headers, dict) or not headers:
        raise GatewayRequestError("no headers on the request")
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == SIGNATURE_HEADER:
            if not isinstance(value, str):
                raise GatewayRequestError("signature header is not a string")
            return value
    raise GatewayRequestError("no signature header on the request")


def raw_body(event: object) -> bytes:
    """Return the bytes the sender actually signed.

    `isBase64Encoded` is set by the gateway and not by the sender, so a handler
    that ignores it verifies a signature over the wrong bytes whenever the
    gateway decides the payload is binary. The failure is total rather than
    partial. Every signature fails and nothing in the application looks broken,
    because a signature that does not match is exactly what an attacker
    produces.
    """
    if not isinstance(event, dict):
        raise GatewayRequestError("gateway event is not an object")
    body = event.get("body")
    if body is None:
        raise GatewayRequestError("no body on the request")
    if event.get("isBase64Encoded"):
        if isinstance(body, bytes):
            body = body.decode("ascii", errors="replace")
        try:
            return base64.b64decode(body, validate=True)
        except (ValueError, TypeError) as exc:
            raise GatewayRequestError("body is flagged base64 and is not") from exc
    if isinstance(body, bytes):
        return body
    if not isinstance(body, str):
        raise GatewayRequestError("body is neither bytes nor a string")
    # The gateway only leaves a body as text when it believes the text is valid
    # in this encoding, so a failure here is the gateway contradicting itself
    # and is worth a refusal rather than a replacement character.
    try:
        return body.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise GatewayRequestError("body is not encodable as utf-8") from exc


def proxy_response(status: int, outcome: str, reason: str = "") -> dict:
    """Shape a payload format 2.0 response.

    The body is JSON because something has to read these in CloudWatch and a
    bare string is harder to query than one field. Stripe reads the status and
    ignores the body.
    """
    payload = {"outcome": outcome}
    if reason:
        payload["reason"] = reason
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }


def handle_event(event: object, handler: WebhookHandler, now: float) -> dict:
    """The whole path, with the clock and the handler passed in.

    Separate from lambda_handler so a test can drive it without an environment
    and without a wall clock. The entry point below is then thin enough to read
    in one go.
    """
    try:
        body = raw_body(event)
        header = signature_header(event.get("headers") if isinstance(event, dict) else None)
    except GatewayRequestError as exc:
        # 400 rather than 500. Nothing about this request will succeed on a
        # retry and a 5xx would tell Stripe to try the same bytes again.
        return proxy_response(400, "malformed", exc.reason)

    response = handler.handle(body, header, now)
    return proxy_response(response.status, response.outcome, response.reason)


def ledger_path() -> str:
    """Where the ledger goes, as its own function so the fallback is gradeable.

    Pulled out of build_handler because building a handler opens a database,
    and a rule worth testing should not need a file on disk to test it.
    """
    return os.environ.get(LEDGER_PATH_VAR, "").strip() or DEFAULT_LEDGER_PATH


def build_handler() -> WebhookHandler:
    return WebhookHandler(EventStore(ledger_path()), config.signing_secrets())


def lambda_handler(event, context):  # noqa: ANN001, ANN201
    import time

    return handle_event(event, build_handler(), time.time())
