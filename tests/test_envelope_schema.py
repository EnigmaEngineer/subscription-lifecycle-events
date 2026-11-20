"""The registered schema against the handler, and the one place they disagree.

A schema in a registry that nothing evaluates is a declared extent. These tests
evaluate it, and the reason they are worth having is the last one. The handler
answered Stripe with a 200 for an envelope the schema refuses, so the
acknowledgement and the contract do not say the same thing about the same bytes.
"""

from __future__ import annotations

import base64
import glob
import json
import os

import pytest

from subevents import config, envelope_schema, signature
from subevents.store import EventStore
from subevents.webhook import WebhookHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = sorted(glob.glob(os.path.join(ROOT, "fixtures", "signed", "*.json")))
SECRET = config.FIXTURE_SECRETS[0]


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_checker_implements_every_keyword_the_schema_uses():
    """The guard that stops this becoming a checker nobody notices is partial.

    If somebody adds a pattern or an enum to the schema file, the registry will
    enforce it and this module will ignore it, and the two will drift with
    nothing complaining.

    Asserting that the difference is empty was the first version and it was
    worthless. A mutation pass on 2026-11-20 broke the walker so it returned an
    empty set, and an empty set has an empty difference, so the guard passed
    against a function that had stopped looking. The set is pinned now.
    """
    used = envelope_schema.keywords_used(envelope_schema.load_schema())
    assert used == {
        "$schema",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "minLength",
    }
    assert used - envelope_schema.IMPLEMENTED_KEYWORDS == set()


def test_the_walker_reaches_a_keyword_nested_two_levels_down():
    """The planted case. The schema's own deepest keyword is three levels in.

    Without this the walker could stop at the top level and the test above would
    still pass, because the top level happens to carry every keyword name the
    schema uses.
    """
    planted = {
        "type": "object",
        "properties": {"outer": {"type": "object", "properties": {"inner": {"enum": [1]}}}},
    }
    assert "enum" in envelope_schema.keywords_used(planted)

    # A dict valued keyword that is not `properties`. The walker has two
    # branches and only this shape tells them apart, which is why both survived
    # a mutation pass until it was planted. Under the wrong branch the walker
    # descends into the values of this dict instead of the dict itself and
    # loses every key inside it.
    other = {"type": "array", "items": {"pattern": "^evt_"}}
    assert envelope_schema.keywords_used(other) == {"type", "items", "pattern"}
    assert envelope_schema.types_used({"items": {"type": "string"}}) == {"string"}


def test_the_checker_answers_for_every_type_the_schema_names():
    used = envelope_schema.types_used(envelope_schema.load_schema())
    assert used == {"object", "string", "integer", "boolean", "null"}
    assert used - envelope_schema.IMPLEMENTED_TYPES == set()


def test_every_implemented_type_arm_is_graded():
    """One assertion per arm, including the two the schema file does not use.

    `number` and `array` are implemented and unused here. A mutation pass found
    the number arm entirely ungraded, which is a fair description of code that
    exists and is never run. They are kept rather than deleted because they are
    two of the seven JSON types and the next schema may well name them, and
    kept means graded.
    """
    cases = {
        "object": ({}, "x"),
        "array": ([], {}),
        "string": ("x", 1),
        "number": (1.5, "1.5"),
        "integer": (1, 1.5),
        "boolean": (True, 1),
        "null": (None, 0),
    }
    assert set(cases) == set(envelope_schema.IMPLEMENTED_TYPES)
    for name, (matching, not_matching) in cases.items():
        assert envelope_schema.violations(matching, {"type": name}) == [], name
        assert envelope_schema.violations(not_matching, {"type": name}) == [
            f"envelope is not {name}"
        ], name

    # A bool is an int in Python and is neither an integer nor a number in JSON.
    assert envelope_schema.violations(True, {"type": "number"}) == ["envelope is not number"]
    assert envelope_schema.violations(True, {"type": "integer"}) == ["envelope is not integer"]

    # The union the schema uses on api_version.
    assert envelope_schema.violations(None, {"type": ["string", "null"]}) == []
    assert envelope_schema.violations("x", {"type": ["string", "null"]}) == []
    assert envelope_schema.violations(1, {"type": ["string", "null"]}) == [
        "envelope is not ['string', 'null']"
    ]


def test_minlength_is_graded_at_the_boundary():
    """One character, because the boundary is where a comparison operator lives.

    `<` against a minLength of 1 refuses the empty string and accepts one
    character. `<=` refuses both and every test here passed anyway, because
    every id in the corpus is longer than that. Same shape as the replay window
    boundary found on 2026-11-19.
    """
    schema = {"type": "string", "minLength": 1}
    assert envelope_schema.violations("", schema) == ["envelope is shorter than 1"]
    assert envelope_schema.violations("a", schema) == []
    assert envelope_schema.violations("ab", schema) == []


def test_the_checker_refuses_the_documents_it_is_supposed_to_refuse():
    """Planted documents, because a checker that matches nothing conforms to everything."""
    good = {
        "id": "evt_1",
        "type": "customer.subscription.created",
        "created": 1763000000,
        "data": {"object": {"id": "sub_1"}},
    }
    assert envelope_schema.conforms(good)

    assert envelope_schema.violations({**good, "id": ""}) == ["id is shorter than 1"]
    assert envelope_schema.violations({**good, "created": "1763000000"}) == [
        "created is not integer"
    ]
    # true is an int in Python and is not an integer in JSON.
    assert envelope_schema.violations({**good, "created": True}) == ["created is not integer"]
    assert envelope_schema.violations({**good, "data": {}}) == ["data.object is missing"]
    assert envelope_schema.violations([good]) == ["envelope is not object"]

    missing = envelope_schema.violations({k: v for k, v in good.items() if k != "type"})
    assert missing == ["type is missing"]


@pytest.mark.parametrize(
    "path",
    [p for p in FIXTURES if load(p)["expect"]["outcome"] in ("accepted", "ignored")],
    ids=lambda p: os.path.basename(p),
)
def test_every_delivery_the_handler_accepts_conforms_to_the_schema(path):
    body = base64.b64decode(load(path)["body_b64"])
    assert envelope_schema.conforms(json.loads(body))


def test_the_schema_refuses_nothing_the_handler_had_not_already_refused():
    """Measured over the corpus. The schema adds no refusals of its own here.

    That is not a complaint about the schema. It is the honest reading of what
    registering it bought on this corpus, which is a contract for consumers
    rather than a second gate at the door.
    """
    schema_only = []
    for path in FIXTURES:
        fixture = load(path)
        body = base64.b64decode(fixture["body_b64"])
        handler = WebhookHandler(EventStore(), list(config.FIXTURE_SECRETS))
        response = handler.handle(body, fixture["header"], fixture["now"])
        acknowledged = response.outcome in ("accepted", "duplicate", "ignored")
        try:
            conforms = envelope_schema.conforms(json.loads(body))
        except ValueError:
            conforms = False
        if acknowledged and not conforms:
            schema_only.append(fixture["name"])

    assert schema_only == []


def test_an_envelope_with_no_data_is_acknowledged_and_does_not_conform():
    """The disagreement, and it is the one with a cost attached.

    The handler reads id, type and created, deliberately and for a stated
    reason. The schema requires data as well, because a consumer that cannot
    reach data.object has nothing to reconstruct from. So this envelope gets a
    200 and a row in the event log, and then fails at the producer against the
    contract the registry holds.

    The 2026-11-18 audit named this envelope as a known limitation and put it in
    the README. Registering the schema is what turned it from a limitation into
    two components that answer differently.
    """
    envelope = {
        "id": "evt_no_data",
        "type": "customer.subscription.updated",
        "created": 1763000000,
    }
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    header = signature.sign(body, SECRET, 1763000000)

    handler = WebhookHandler(EventStore(), [SECRET])
    response = handler.handle(body, header, 1763000001)

    assert response.status == 200
    assert response.outcome == "accepted"
    assert envelope_schema.violations(envelope) == ["data is missing"]
