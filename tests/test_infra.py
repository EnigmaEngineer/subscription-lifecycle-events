"""Grade the configuration, then grade the rules that grade it.

Every rule here passes against the committed `infra/`. That on its own proves
nothing, because a rule whose pattern no longer matches also passes. So each
rule is also pointed at a copy of the configuration with its own defect planted
in it and has to refuse that copy.

The planted defects are not invented. They are the eight that
`terraform validate` accepted on 2026-11-20 when they were applied to this
configuration one at a time.
"""

from __future__ import annotations

import re

import pytest

from subevents import infra_rules


@pytest.fixture(scope="module")
def files():
    return infra_rules.load()


def test_every_rule_passes_the_committed_configuration(files):
    failed = [finding for finding in infra_rules.run(files) if not finding.ok]
    assert failed == [], [f"{f.rule}: {f.detail}" for f in failed]


def test_the_rule_list_is_not_empty_and_every_rule_is_named():
    assert len(infra_rules.RULES) == 10
    names = {rule(infra_rules.load()).rule for rule in infra_rules.RULES}
    assert len(names) == 10


PLANTED = infra_rules.PLANTED


def test_every_rule_has_a_planted_defect_to_refuse():
    named = {rule(infra_rules.load()).rule for rule in infra_rules.RULES}
    assert set(PLANTED) == named


@pytest.mark.parametrize("rule_name", sorted(PLANTED))
def test_the_rule_refuses_its_planted_defect(files, rule_name):
    target, plant = PLANTED[rule_name]
    broken = dict(files)
    broken[target] = plant(files[target])
    assert broken[target] != files[target], "the plant changed nothing, so the rule is ungraded"

    findings = {finding.rule: finding for finding in infra_rules.run(broken)}
    assert findings[rule_name].ok is False, findings[rule_name].detail


def test_a_rule_reading_its_own_comment_would_be_caught(files):
    """The comment above a line usually contains the literal the rule looks for.

    Planting the defect in the code while leaving the comment intact is the case
    that catches a rule matching prose, and it is why the rules strip whole line
    comments first.
    """
    broken = dict(files)
    broken["glue.tf"] = files["glue.tf"].replace(
        'compatibility = "BACKWARD"', 'compatibility = "NONE"'
    )
    assert "BACKWARD and not NONE" in broken["glue.tf"]
    findings = {finding.rule: finding for finding in infra_rules.run(broken)}
    assert findings["schema_compatibility_is_enforced"].ok is False


# The terraform half. There is no terraform on the machine the daily build runs
# on, so these read the manifest scripts/validate_infra.py writes where
# terraform is. The split keeps the suite runnable on a clone with nothing
# installed, and it means these tests grade a measurement rather than make one.

import json
import os

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "measurements",
    "terraform.json",
)


@pytest.fixture(scope="module")
def terraform_manifest():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_configuration_is_formatted_and_valid(terraform_manifest):
    assert terraform_manifest["fmt_clean"] is True
    assert terraform_manifest["validate_clean"] is True


def test_the_control_was_refused(terraform_manifest):
    """Without this the rest of the manifest cannot be read.

    A misspelled attribute has to be refused. A manifest saying validate caught
    nothing looks identical whether validate is permissive or whether it never
    ran, so the control is what makes the other numbers mean something.
    """
    assert terraform_manifest["control_refused"] is True
    assert terraform_manifest["control_error"] == "Unsupported attribute"


def test_the_manifest_covers_exactly_the_rules_in_this_file(terraform_manifest):
    """A rule added here without a terraform run behind it is ungraded."""
    assert set(terraform_manifest["defects"]) == set(PLANTED)


def test_the_rules_carry_the_defects_terraform_accepted(terraform_manifest):
    """The reason this module exists, stated as a number rather than an opinion.

    Nine of the ten planted defects are configurations terraform called valid.
    Every one of those nine is refused by a rule above. If terraform ever starts
    catching one of them, this still passes and the count in the README moves,
    which is the right way round.
    """
    missed = [
        rule
        for rule, result in terraform_manifest["defects"].items()
        if not result["caught_by_terraform"]
    ]
    assert len(missed) == terraform_manifest["missed_by_terraform"]
    assert terraform_manifest["missed_by_terraform"] == 9
    assert terraform_manifest["caught_by_terraform"] == 1

    files = infra_rules.load()
    for rule in missed:
        target, plant = PLANTED[rule]
        broken = dict(files)
        broken[target] = plant(files[target])
        findings = {finding.rule: finding for finding in infra_rules.run(broken)}
        assert findings[rule].ok is False, f"{rule} is missed by terraform and by its own rule"


# Five survivors from a mutation pass over the first twelve sites on
# 2026-11-20. Four of them are the same shape and it is the shape that keeps
# turning up: a rule graded only by a planted defect miles from its boundary,
# and an absent-value branch nothing ever reaches.


def test_the_gateway_ceiling_is_graded_at_its_own_boundary():
    """29 passes and 30 fails, and nothing was asserting either.

    The planted defect uses 60, which is refused whether the ceiling is 30 or
    31, so the constant itself was a number nothing read. The ceiling is not
    arbitrary. The HTTP API gives up at 30 seconds, so 30 is already
    unreachable and the rule has to refuse it.
    """
    files = infra_rules.load()
    for value, expected in ((29, True), (30, False), (31, False)):
        broken = dict(files)
        broken["lambda.tf"] = files["lambda.tf"].replace(
            "timeout     = 29", f"timeout     = {value}"
        )
        finding = infra_rules.lambda_timeout_under_gateway_ceiling(broken)
        assert finding.ok is expected, f"{value}s came back {finding.detail}"

    assert infra_rules.GATEWAY_INTEGRATION_CEILING == 30


def test_a_rule_with_nothing_to_read_fails_rather_than_passes():
    """The absent-value branches, which no planted defect reaches.

    Every plant changes a value. None of them delete the line, so the branch
    that handles a missing setting had never been run, and a rule that returns
    ok when it found nothing to grade is the gate-that-passes-on-nothing shape
    this program keeps meeting.
    """
    files = infra_rules.load()

    no_timeout = dict(files)
    no_timeout["lambda.tf"] = re.sub(r"\n\s*timeout     = \d+", "", files["lambda.tf"])
    finding = infra_rules.lambda_timeout_under_gateway_ceiling(no_timeout)
    assert finding.ok is False
    assert finding.detail == "no timeout is set"

    no_compat = dict(files)
    no_compat["glue.tf"] = re.sub(r'\n\s*compatibility = "[A-Z_]+"', "", files["glue.tf"])
    finding = infra_rules.schema_compatibility_is_enforced(no_compat)
    assert finding.ok is False
    assert finding.detail == "no compatibility is set"

    no_format = dict(files)
    no_format["apigateway.tf"] = re.sub(
        r'\n\s*payload_format_version = "[\d.]+"', "", files["apigateway.tf"]
    )
    finding = infra_rules.payload_format_matches_the_decoder(no_format)
    assert finding.ok is False
    assert finding.detail == "no payload format version is set"


def test_a_finding_cannot_be_rewritten_after_it_is_made():
    """Frozen, because a findings list is the thing a report is built from."""
    finding = infra_rules.Finding("x", True, "y")
    with pytest.raises(Exception):
        finding.ok = False
