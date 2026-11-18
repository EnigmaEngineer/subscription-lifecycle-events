"""Where the webhook signing secrets come from.

They used to be literals in three files. GitHub's secret scanner opened three
alerts against this repository on the day it was first pushed, because the
values were shaped like real Stripe webhook secrets. They were invented and were
never live anywhere, so nothing was exposed, and the alert was still right about
the thing it checks. A string that looks like a credential sitting in a public
repository is a cost whether or not it opens a door.

Two changes came out of that. Secrets are read from the environment here rather
than typed anywhere else, and the fixture defaults are deliberately not in
Stripe's format. HMAC does not care what the key looks like, so there was never
a reason for a test key to be shaped like a live one.

    export SUBEVENTS_WEBHOOK_SECRETS=whsec_first,whsec_second
"""

from __future__ import annotations

import os

ENV_VAR = "SUBEVENTS_WEBHOOK_SECRETS"

# The live secret first and the retired one second, which is the order an
# endpoint holds them in during a rotation.
FIXTURE_SECRETS = (
    "fixture-signing-key-current-do-not-use",
    "fixture-signing-key-retired-do-not-use",
)

# A key the endpoint was never given. Used to sign the fixture that has to fail.
FIXTURE_WRONG_SECRET = "fixture-signing-key-never-configured"


def signing_secrets() -> tuple[str, ...]:
    """Return the secrets this endpoint will accept.

    Falls back to the fixture keys so the suite and the generators run on a
    machine with nothing configured. A real deployment sets the variable and a
    deployment that forgets gets keys that cannot verify anything Stripe sent,
    which fails loudly on the first delivery rather than quietly accepting.
    """
    configured = os.environ.get(ENV_VAR, "").strip()
    if not configured:
        return FIXTURE_SECRETS
    return tuple(part.strip() for part in configured.split(",") if part.strip())
