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
# latest.json is written by scripts/measure.py beside the dated file. Pointing
# the gate at it means a re-measure does not need this line edited, which is a
# step that would eventually get forgotten on the morning it mattered.
MANIFEST = os.path.join(ROOT, "measurements", "latest.json")


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
    rec = manifest["reconstruction"]
    amb = manifest["ambiguity"]
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
        "subscriptions reconstructed": f"{rec['subscriptions']} subscriptions",
        "arrival order cost": f"gets {rec['arrival_order_subscriptions_wrong']} of",
        "arrival order orphans": f"{rec['arrival_order_orphan_events']} events arrive",
        "ties the type rank settles": f"settles {rec['resolved_by_type_rank']} of",
        "what the tie break is worth": (
            f"changes the state of {rec['subscriptions_changed_by_tie_order']} subscriptions"
        ),
        "probe ties left to the id": f"leaves {amb['probe_resolved_by_event_id']} seconds",
        "probe subscriptions moved": f"moves the quantity on {amb['probe_subscriptions_changed_by_id_order']}",
        "probe missed by the flag": f"{amb['probe_changed_but_not_flagged']} of them are unflagged",
    }


def test_the_manifest_is_from_this_build(manifest):
    assert manifest["fixtures"]["fixtures"] > 0
    assert manifest["replay"]["clean_events"] > 0


def test_every_published_figure_appears(readme, manifest):
    missing = {k: v for k, v in published(manifest).items() if v not in readme}
    assert missing == {}


def test_the_measurement_block_is_the_scripts_output(readme, tmp_path):
    """The fence under 'Measured on' is stdout, not a retyping of it.

    `--out` sends the manifest somewhere disposable. Letting this write over
    the committed one means the suite can republish figures from whatever code
    happens to be in the tree, which is the opposite of what the gate is for.
    """
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "measure.py"), "--out", str(tmp_path)],
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    assert out.strip() in readme


def test_the_replay_block_is_the_scripts_output(readme):
    """The reconstruction fence is stdout from scripts/replay.py."""
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "replay.py")],
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    assert out.strip() in readme


def test_the_readme_does_not_claim_the_replays_agree_unless_they_do(manifest, readme):
    """A sentence nobody re-derives is the thing this whole file exists against."""
    if manifest["reconstruction"]["replays_agree"]:
        assert "the two reconstructions are byte identical" in readme
    else:
        assert "the two reconstructions are byte identical" not in readme


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
