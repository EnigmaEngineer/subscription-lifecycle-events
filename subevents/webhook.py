"""The webhook endpoint, minus the web server.

Everything here takes raw bytes and a header string and returns a status code
and a reason. There is no framework in the way, which means the tests drive the
same function a request would and the whole path is exercised without a socket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from subevents import signature
from subevents.store import Decision, EventStore

# Types this platform reconstructs state from. An event outside the set is
# signed and real and still not ours, so it is acknowledged and dropped. Stripe
# sends whatever the account is subscribed to and silence is not an option.
HANDLED_TYPES = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
    }
)


@dataclass(frozen=True)
class Response:
    status: int
    outcome: str
    reason: str = ""

    @property
    def acknowledged(self) -> bool:
        return 200 <= self.status < 300


class WebhookHandler:
    def __init__(
        self,
        store: EventStore,
        secrets: list[str] | tuple[str, ...] | str,
        tolerance: int = signature.DEFAULT_TOLERANCE_SECONDS,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.tolerance = tolerance

    def handle(self, body: bytes, sig_header: str, now: float) -> Response:
        try:
            signed_at = signature.verify(
                body, sig_header, self.secrets, now=now, tolerance=self.tolerance
            )
        except signature.SignatureError as exc:
            # 400 and not 401. Stripe retries on anything that is not a 2xx and
            # a body whose bytes failed once will fail every time, so asking for
            # the same request again buys nothing.
            return Response(400, "rejected", exc.reason)

        try:
            envelope = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            # Signed, so the sender is who they claim. The bytes are still
            # unusable and identical bytes will not parse on a second attempt.
            return Response(400, "unparseable", str(exc))

        problem = _shape_problem(envelope)
        if problem:
            return Response(400, "unparseable", problem)

        event_id = envelope["id"]
        event_type = envelope["type"]

        if event_type not in HANDLED_TYPES:
            # Acknowledged on purpose. A 500 here would make Stripe retry an
            # event this platform is never going to want.
            return Response(200, "ignored", event_type)

        outcome = self.store.accept(
            event_id=event_id,
            event_type=event_type,
            created=int(envelope["created"]),
            received_at=int(signed_at),
            payload=body,
        )

        if outcome.decision is Decision.NEW:
            return Response(200, "accepted")
        if outcome.decision is Decision.DUPLICATE:
            return Response(200, "duplicate", f"delivery {outcome.delivery_count}")
        return Response(200, "conflict", "same id, different body")


def _shape_problem(envelope: object) -> str:
    """Return a reason the envelope is unusable, or an empty string.

    Deliberately shallow. It checks the four fields the handler itself reads and
    nothing inside data.object, because the shape of the object is Stripe's and
    pinning it here would turn every product change into a 400.
    """
    if not isinstance(envelope, dict):
        return "body is not a json object"
    for field in ("id", "type", "created"):
        if field not in envelope:
            return f"missing field {field}"
    if not isinstance(envelope["id"], str) or not envelope["id"]:
        return "id is not a non empty string"
    if not isinstance(envelope["type"], str) or not envelope["type"]:
        return "type is not a non empty string"
    if isinstance(envelope["created"], bool) or not isinstance(envelope["created"], int):
        return "created is not an integer"
    return ""
