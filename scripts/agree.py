"""Check this repository's HMAC against OpenSSL rather than against itself.

tests/test_signature.py signs with subevents.signature and verifies with the
same module, which proves the two halves agree and proves nothing about whether
either matches Stripe. This recomputes every signature in the fixture corpus
with the openssl command line and compares the hex digests.

It is not a test file because it shells out to a binary that may not be on the
machine running the suite. A missing openssl here is reported as a skip with a
count, not as a pass.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from subevents import config, signature  # noqa: E402

SECRETS = {
    "live": config.FIXTURE_SECRETS[0],
    "rotated": config.FIXTURE_SECRETS[1],
    "wrong": config.FIXTURE_WRONG_SECRET,
}


def openssl_hmac(message: bytes, secret: str) -> str:
    out = subprocess.run(
        ["openssl", "dgst", "-sha256", "-hmac", secret],
        input=message,
        capture_output=True,
        check=True,
    )
    return out.stdout.decode().strip().rsplit("= ", 1)[-1]


def main() -> int:
    if shutil.which("openssl") is None:
        print("openssl is not on this machine. 0 comparisons made.")
        return 0

    version = subprocess.run(["openssl", "version"], capture_output=True, check=True)
    print(version.stdout.decode().strip())

    compared = 0
    disagreed = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json"))):
        fixture = json.load(open(path, encoding="utf-8"))
        body = base64.b64decode(fixture["body_b64"])
        header = fixture["header"]
        stamps = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("t=")]
        if not stamps:
            continue
        try:
            timestamp = int(stamps[0])
        except ValueError:
            continue
        message = str(timestamp).encode("ascii") + b"." + body
        for name, secret in SECRETS.items():
            mine = signature.expected_signature(body, timestamp, secret)
            theirs = openssl_hmac(message, secret)
            compared += 1
            if mine != theirs:
                disagreed += 1
                print(f"DISAGREE {os.path.basename(path)} secret={name}")
                print(f"  python  {mine}")
                print(f"  openssl {theirs}")

    print(f"{compared} comparisons, {disagreed} disagreements")
    return 1 if disagreed else 0


if __name__ == "__main__":
    raise SystemExit(main())
