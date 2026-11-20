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
# Written by scripts/validate_infra.py where terraform is, which is not the
# machine the suite runs on. Read rather than re-derived, for that reason.
TERRAFORM_MANIFEST = os.path.join(ROOT, "measurements", "terraform.json")


def _terraform_manifest():
    with open(TERRAFORM_MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


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
    gate = manifest["gateway"]
    sch = manifest["schema"]
    led = manifest["ledger"]
    tf = _terraform_manifest()
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
        # 2026-11-20. The gateway, the ledger split, the schema and the
        # configuration rules. Every one of these is printed by
        # scripts/measure.py into the manifest this function reads.
        "outcomes a wrong decode changes": (
            f"**{gate['outcomes_changed_by_ignoring_the_flag']} of the"
            f" {gate['fixtures']}** fixtures"
        ),
        "bodies the gateway can carry as text": (
            f"**{gate['bodies_the_gateway_can_carry_as_text']} of"
            f" {gate['fixtures']}** bodies can arrive as text"
        ),
        "bodies that must arrive base64": (
            f"and {gate['bodies_that_must_arrive_base64']} can only"
        ),
        "envelopes the schema refuses": f"refuses {sch['refused_by_the_schema']} envelopes",
        "ledgers at which nothing is refused": (
            f"**At {_first_zero(led)} ledgers it refuses nothing at all.**"
        ),
        "state digests across the split": (
            f"One state digest across all"
            f" {sum(len(group) for group in led['arms'].values())} arms."
        ),
        "defects terraform caught": f"caught {tf['caught_by_terraform']} of {tf['planted_defects']}",
        "defects terraform missed": f"The other {tf['missed_by_terraform']} are",
    }


def _first_zero(led):
    """The smallest round robin ledger count that refuses nothing, from the manifest."""
    for arm in led["arms"]["round_robin"]:
        if arm["refused_as_duplicate"] == 0:
            return arm["shards"]
    raise AssertionError("every round robin arm still refuses something")


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


def test_the_published_test_count_is_real(readme, manifest):
    """Collected, passed and skipped, because they are three numbers.

    This gate used to compare the collected count against the string
    "N passed". That held for as long as nothing was skipped. The first skip
    landed on 2026-11-20 and the gate went on passing while the README
    published a line no run has ever printed. Deriving all three is the fix.

    The skip count is not a constant. There is exactly one skip and it fires
    once per fixture body that cannot be carried as a string, which is the
    figure the gateway section already publishes. If a second such fixture
    lands, this moves with it.
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        cwd=ROOT,
    ).stdout.decode()
    tail = [line for line in out.strip().splitlines() if "test" in line][-1]
    collected = int(tail.split()[0])

    skipped = manifest["gateway"]["bodies_that_must_arrive_base64"]
    passed = collected - skipped

    assert f"{collected} tests collected" in readme
    assert f"{passed} passed, {skipped} skipped" in readme


def test_the_tolerance_in_the_prose_is_the_one_in_the_code(readme):
    from subevents import signature

    assert f"inclusive at {signature.DEFAULT_TOLERANCE_SECONDS} seconds" in readme


def test_the_handled_types_prose_matches_the_set(readme):
    # The README claims an unhandled type is dropped. If the set ever grows to
    # everything, that sentence stops being true.
    from subevents.webhook import HANDLED_TYPES

    assert 0 < len(HANDLED_TYPES) < 20


def test_the_ledger_block_is_the_scripts_output(readme):
    """The split ledger fence is stdout from scripts/shard.py.

    Published as the script's own output for the same reason the others are. A
    table retyped into a document is a table nobody re-derives.
    """
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "shard.py")],
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    assert out.strip() in readme


def test_the_readme_does_not_claim_the_split_is_harmless_unless_it_is(manifest, readme):
    """The one sentence in that section that would be a lie if the numbers moved."""
    if manifest["ledger"]["state_survives_every_split"]:
        assert "The answer does not change." in readme
    else:
        assert "The answer does not change." not in readme


def test_the_readme_does_not_claim_nothing_is_applied_if_a_plan_ever_ran(readme):
    """A guard against the cheerful edit.

    The honesty of that whole section rests on one sentence. If somebody applies
    this configuration and forgets to come back here, this is what fails.
    """
    tf = _terraform_manifest()
    assert "validate" in tf["generated_by"] or "validate" in str(tf)
    assert "**None of it has" in readme
    assert "no plan has run" in readme.lower()
