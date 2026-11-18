"""Refuse to ship a string shaped like a credential.

GitHub's secret scanner opened three alerts against this repository on the day
it was first pushed. The flagged values were invented and had never been live,
so nothing was exposed. The alert was still correct about what it checks, and
fixing only the three instances would leave the next one to be found by the
scanner again.

So the rule is mechanical now. No file here may carry a string matching a
provider's credential format. The fixture keys in `subevents/config.py` are
deliberately shaped so that no scanner would look twice at them, and HMAC does
not care what a key looks like.

The patterns below do not match themselves, because none of them is followed by
a long enough run of alphanumerics.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "data"}

# Shapes this repository could plausibly produce. Stripe first, because that is
# the sender it talks to, then the two that turn up in any python project.
CREDENTIAL_SHAPES = {
    "stripe webhook signing secret": r"whsec_[A-Za-z0-9]{24,}",
    "stripe secret key": r"[sr]k_(live|test)_[A-Za-z0-9]{20,}",
    "aws access key id": r"AKIA[0-9A-Z]{16}",
    "github token": r"gh[pousr]_[A-Za-z0-9]{36,}",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}

COMPILED = {name: re.compile(pattern) for name, pattern in CREDENTIAL_SHAPES.items()}


def repository_files():
    found = []
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            path = os.path.join(folder, name)
            if os.path.getsize(path) > 2_000_000:
                continue
            found.append(path)
    return found


def test_the_scan_sees_the_repository():
    # A walk that returns nothing passes every assertion below it.
    paths = repository_files()
    assert len(paths) >= 40
    assert any(p.endswith("subevents/config.py") for p in paths)


def test_no_file_carries_a_credential_shaped_string():
    hits = []
    for path in repository_files():
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in COMPILED.items():
            match = pattern.search(text)
            if match:
                hits.append((os.path.relpath(path, ROOT), name, match.group()[:12]))
    assert hits == []


def test_the_patterns_actually_match_what_they_claim_to():
    """A scanner that matches nothing passes on an exposed key.

    These are synthetic strings built at runtime so the file itself stays clean.
    """
    samples = {
        "stripe webhook signing secret": "whsec_" + "A" * 32,
        "stripe secret key": "sk_live_" + "B" * 24,
        "aws access key id": "AKIA" + "C" * 16,
        "github token": "ghp_" + "D" * 36,
        "private key block": "-" * 5 + "BEGIN RSA PRIVATE KEY" + "-" * 5,
    }
    for name, sample in samples.items():
        assert COMPILED[name].search(sample), name


def test_the_fixture_keys_are_not_credential_shaped():
    from subevents import config

    for key in config.FIXTURE_SECRETS + (config.FIXTURE_WRONG_SECRET,):
        for name, pattern in COMPILED.items():
            assert not pattern.search(key), f"{key} matches {name}"


def test_secrets_come_from_the_environment_when_it_is_set(monkeypatch):
    from subevents import config

    monkeypatch.setenv(config.ENV_VAR, "first_key, second_key ")
    assert config.signing_secrets() == ("first_key", "second_key")
    monkeypatch.setenv(config.ENV_VAR, "   ")
    assert config.signing_secrets() == config.FIXTURE_SECRETS
