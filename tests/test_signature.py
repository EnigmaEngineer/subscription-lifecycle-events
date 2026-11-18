import base64
import json
import os

import pytest

from subevents import config, signature

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET, ROTATED = config.FIXTURE_SECRETS

# Taken from `openssl dgst -sha256 -hmac` on this machine on 2026-11-18 against
# the bytes of fixtures/signed/valid-single-secret.json. OpenSSL 3.0.2. This is
# the only vector in the suite that did not come out of the module it tests.
OPENSSL_VECTOR = "ec6fce40f3af9101f94cfd6e8c812171acb525742344b433cccc40dc7fc0b05d"


def load(name):
    with open(os.path.join(ROOT, "fixtures", "signed", name + ".json"), encoding="utf-8") as h:
        fixture = json.load(h)
    return base64.b64decode(fixture["body_b64"]), fixture["header"], fixture["now"]


def test_matches_an_independent_implementation():
    body, header, _ = load("valid-single-secret")
    timestamp = signature.parse_header(header).timestamp
    assert signature.expected_signature(body, timestamp, SECRET) == OPENSSL_VECTOR


def test_roundtrip():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000000)
    assert signature.verify(body, header, SECRET, now=1763000001) == 1763000000


def test_two_signatures_during_rotation():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000000, extra_secret=ROTATED)
    # The endpoint holds only the retired secret and the header carries both.
    assert signature.verify(body, header, [ROTATED], now=1763000001) == 1763000000


def test_endpoint_with_two_secrets_accepts_either():
    body = b'{"id":"evt_x"}'
    for secret in (SECRET, ROTATED):
        header = signature.sign(body, secret, 1763000000)
        assert signature.verify(body, header, [SECRET, ROTATED], now=1763000001)


def test_tolerance_is_inclusive_at_the_boundary():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000000)
    assert signature.verify(body, header, SECRET, now=1763000300, tolerance=300)
    with pytest.raises(signature.SignatureError) as exc:
        signature.verify(body, header, SECRET, now=1763000301, tolerance=300)
    assert exc.value.reason == "timestamp outside tolerance"


def test_a_clock_ahead_of_ours_is_refused_too():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000900)
    with pytest.raises(signature.SignatureError) as exc:
        signature.verify(body, header, SECRET, now=1763000000, tolerance=300)
    assert exc.value.reason == "timestamp is in the future"


def test_one_byte_of_body_breaks_it():
    body = b'{"id":"evt_x","amount":1900}'
    header = signature.sign(body, SECRET, 1763000000)
    tampered = body.replace(b"1900", b"1901")
    assert len(tampered) == len(body)
    with pytest.raises(signature.SignatureError):
        signature.verify(tampered, header, SECRET, now=1763000001)


def test_signature_is_case_sensitive():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000000).upper()
    with pytest.raises(signature.SignatureError):
        signature.verify(body, header, SECRET, now=1763000001)


def test_no_secret_configured_is_an_error_and_not_an_accept():
    body = b'{"id":"evt_x"}'
    header = signature.sign(body, SECRET, 1763000000)
    with pytest.raises(signature.SignatureError) as exc:
        signature.verify(body, header, [], now=1763000001)
    assert exc.value.reason == "no signing secret configured"


@pytest.mark.parametrize(
    "header,reason",
    [
        ("", "empty signature header"),
        ("   ", "empty signature header"),
        ("v1=abc", "no timestamp in signature header"),
        ("t=1763000000", "no v1 signature in signature header"),
        ("t=later,v1=abc", "timestamp is not an integer"),
        ("t=1,t=2,v1=abc", "more than one timestamp in signature header"),
        ("t=1763000000,junk,v1=abc", "malformed element in signature header"),
        ("t=1763000000,v1=", "no v1 signature in signature header"),
    ],
)
def test_header_parsing_refusals(header, reason):
    with pytest.raises(signature.SignatureError) as exc:
        signature.parse_header(header)
    assert exc.value.reason == reason


def test_unknown_scheme_is_skipped():
    body = b'{"id":"evt_x"}'
    good = signature.expected_signature(body, 1763000000, SECRET)
    header = f"t=1763000000,v0=legacy,v1={good}"
    parsed = signature.parse_header(header)
    assert parsed.signatures == (good,)
    assert signature.verify(body, header, SECRET, now=1763000001)


def test_whitespace_around_elements_is_tolerated():
    body = b'{"id":"evt_x"}'
    good = signature.expected_signature(body, 1763000000, SECRET)
    header = f"t= 1763000000 , v1= {good} "
    assert signature.verify(body, header, SECRET, now=1763000001)


def test_empty_body_still_signs():
    header = signature.sign(b"", SECRET, 1763000000)
    assert signature.verify(b"", header, SECRET, now=1763000001)
