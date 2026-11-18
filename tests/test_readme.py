"""Every figure the README publishes has to re-derive.

A number typed into a document is a number nobody checks again. The rule here is
that a published figure either appears in PUBLISHED below, where it is derived
from the measurement manifest, or it is not published. A figure with no entry is
ungraded, and an ungraded figure is how a document ends up describing a run that
no longer exists.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
MANIFEST = os.path.join(ROOT, "measurements", "2026-11-18.json")


@pytest.fixture(scope="module")
def readme():
    with open(README, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def thousands(n):
    return f"{n:,}"


def published(manifest):
    """Return every figure the README is allowed to state, as it must appear."""
    fix = manifest["fixtures"]
    rep = manifest["replay"]
    disp = rep["displacement"]
    ties = rep["ties"]
    return {
        "wire deliveries in prose": thousands(rep["wire_deliveries"]),
        "distinct events in prose": thousands(rep["clean_events"]),
        "stored rows in prose": thousands(rep["stored_events"]),
        "fixture corpus size": f"corpus is {fix['fixtures']} signed requests",
        "fixtures that verify": f"{fix['outcomes']['accepted']} of them verify",
        "duplicates in the wire stream": f"carries {disp['duplicates']} duplicates",
        "rank displacement": f"`rank max displacement` is {disp['rank_max_displacement']}",
        "raw displacement": f"`raw max displacement` is {disp['raw_max_displacement']}",
        "tied seconds": f"plants {ties['tied_seconds']} of them",
        "events in ties": f"covering {ties['events_in_ties']} events,",
    }


def test_the_manifest_is_from_this_build(manifest):
    assert manifest["fixtures"]["fixtures"] > 0
    assert manifest["replay"]["clean_events"] > 0


def test_every_published_figure_appears(readme, manifest):
    missing = {k: v for k, v in published(manifest).items() if v not in readme}
    assert missing == {}


def test_the_measurement_block_is_the_scripts_output(readme):
    """The fence under 'Measured on' is stdout, not a retyping of it."""
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "measure.py")],
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    assert out.strip() in readme


def test_the_agreement_block_is_the_scripts_output(readme):
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not on this machine, so the published block cannot be re-derived")
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "agree.py")],
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    assert out.strip() in readme


def test_the_published_test_count_is_real(readme):
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        cwd=ROOT,
    ).stdout.decode()
    tail = [line for line in out.strip().splitlines() if "test" in line][-1]
    collected = int(tail.split()[0])
    assert f"{collected} passed" in readme


def test_the_tolerance_in_the_prose_is_the_one_in_the_code(readme):
    from subevents import signature

    assert f"inclusive at {signature.DEFAULT_TOLERANCE_SECONDS} seconds" in readme


def test_the_handled_types_prose_matches_the_set(readme):
    # The README claims an unhandled type is dropped. If the set ever grows to
    # everything, that sentence stops being true.
    from subevents.webhook import HANDLED_TYPES

    assert 0 < len(HANDLED_TYPES) < 20
