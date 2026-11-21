"""The SQL fold against the Python fold, read back out of the measurement.

`scripts/warehouse.py` needs dbt and duckdb and neither is on every machine
this repository is checked out on, so the comparison runs where they are and
commits a manifest. These checks grade that manifest and the two places the
project copies something the Python module owns.
"""

import csv
import json
import os
import re

from subevents import state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "measurements", "dbt.json")
SEED = os.path.join(ROOT, "dbt", "seeds", "event_type_rank.csv")
PROJECT = os.path.join(ROOT, "dbt", "dbt_project.yml")


def manifest():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_build_succeeded_and_ran_every_node():
    payload = manifest()["dbt"]
    assert payload["exit_code"] == 0
    assert payload["nodes_by_kind"] == {"model": 4, "seed": 1, "test": 13}
    assert payload["nodes_by_status"]["pass"] == 13
    assert payload["nodes_by_status"]["success"] == 5


def test_the_two_folds_agree_on_every_subscription():
    payload = manifest()
    assert payload["subscriptions_compared"] == 200
    assert payload["subscriptions_disagreeing"] == 0
    assert payload["fields_disagreeing"] == {}
    assert payload["folds_agree"] is True
    assert payload["python_state_sha256"] == payload["sql_state_sha256"]


def test_the_warehouse_refuses_the_duplicates_the_sink_does_not():
    """The sink lands every delivery and the staging model is the ledger.

    1627 deliveries, 1501 distinct events, 1501 rows out of staging. The sink
    has no opinion about a Stripe event id, so this is the only thing in the
    chain that does.
    """
    payload = manifest()
    assert payload["landed_records"] == 1627
    assert payload["distinct_event_ids"] == 1501
    assert payload["staging_rows"] == payload["distinct_event_ids"]


def test_both_controls_fire():
    """A comparison reporting no disagreement is also a comparison of nothing.

    The arrival order arm is wrong for 102 of 200 subscriptions and that figure
    was measured independently in Python on 2026-11-19. The flat precedence arm
    rebuilds the models with every event type at one rank, which is the only
    thing that says the models read the seed at all.
    """
    controls = manifest()["controls"]
    assert controls["arrival_order_subscriptions_disagreeing"] == 102
    assert controls["flat_precedence_dbt_exit"] == 0
    assert controls["flat_precedence_subscriptions_disagreeing"] > 0


def test_the_precedence_seed_is_load_bearing_for_one_field_and_no_others():
    """Flatten every rank and the reconstructed state does not move.

    Only `ambiguous_seconds` changes. The rule that 2026-11-19 found changes
    nothing when inverted also changes nothing when deleted, and the only
    thing it feeds is the field whose job is to say the answer was arbitrary.
    """
    fields = manifest()["controls"]["flat_precedence_fields_disagreeing"]
    assert set(fields) == {"ambiguous_seconds"}


def test_the_probe_arm_reaches_the_tie_break_the_corpus_does_not():
    """On the published corpus the sort on the event id decides nothing.

    So the comparison above grades it not at all. This arm plants seconds
    holding two events of one rank, and it is the only place the two
    implementations could legitimately order things differently.
    """
    probe = manifest()["probe"]
    assert probe["dbt_exit"] == 0
    assert probe["ties_left_to_the_event_id"] > 0
    assert probe["subscriptions_disagreeing"] == 0
    assert probe["folds_agree"] is True


def test_the_seed_is_the_handler_s_own_precedence():
    with open(SEED, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    ranks = {row["event_type"]: int(row["type_rank"]) for row in rows}
    invoices = {row["event_type"] for row in rows if row["is_invoice"] == "true"}
    assert ranks == state.TYPE_RANK
    assert invoices == set(state.INVOICE_EVENTS)


def test_the_unranked_fallback_is_written_down_once():
    """The project carries a copy of a Python constant and this is the gate on it."""
    with open(PROJECT, encoding="utf-8") as handle:
        text = handle.read()
    found = re.search(r"^\s*unranked_type_rank:\s*(\d+)\s*$", text, re.M)
    assert found, "the project has to declare the fallback rank"
    assert int(found.group(1)) == state.UNRANKED
    assert manifest()["unranked_type_rank"] == state.UNRANKED


def test_the_models_resolve_to_the_same_names_on_both_targets():
    """Case aside, the two targets write to the same relations.

    duckdb lower cases the source schema because that is what the local
    database holds. Everything the project creates resolves identically.
    """
    resolved = manifest()["resolved"]
    for target in ("duckdb", "snowflake"):
        assert resolved[target]["exit_code"] == 0
    left = {name.split(".", 1)[1] for name in resolved["duckdb"]["models"]}
    right = {name.split(".", 1)[1] for name in resolved["snowflake"]["models"]}
    assert left == right
