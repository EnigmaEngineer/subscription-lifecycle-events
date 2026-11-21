"""The names four files spend against the names one file creates.

Two directions and two exception lists. The reconciliation itself is in
`subevents/names.py` and these are the checks that it read something, that the
exception lists are not stale, and that the relations dbt resolved against the
warehouse target are relations the configuration creates.
"""

import json
import os

from subevents import names

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "measurements", "dbt.json")


def manifest():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def test_every_reconciliation_rule_passes():
    failures = {f.rule: f.detail for f in names.run() if not f.ok}
    assert failures == {}


def test_the_readers_found_the_names_they_should():
    """Pinned rather than counted.

    A reader that silently matches nothing makes every set difference empty,
    and three of the four rules are set differences.
    """
    assert names.created() == {
        names.Name("DATABASE", "SUBEVENTS"),
        names.Name("SCHEMA", "RAW"),
        names.Name("SCHEMA", "STAGING"),
        names.Name("SCHEMA", "ANALYTICS"),
        names.Name("WAREHOUSE", "SUBEVENTS_TRANSFORM_WH"),
        names.Name("ROLE", "KAFKA_CONNECTOR_ROLE"),
        names.Name("ROLE", "DBT_TRANSFORMER_ROLE"),
        names.Name("USER", "KAFKA_CONNECTOR_USER"),
    }


def test_each_of_the_four_spenders_names_something():
    for reader in (
        names.spent_by_the_sink,
        names.spent_by_the_dbt_profile,
        names.spent_by_the_dbt_project,
        names.spent_by_the_dbt_sources,
    ):
        assert reader(), reader.__name__


def test_the_sources_reader_finds_the_relation_whole():
    """Pinned as a set, because two thirds of it is spent by other files too.

    A reader that returned only the table name would leave the database and
    the schema reconciled by the profile and the sink, so the rules would pass
    and nothing would say this file had stopped being read. A mutation
    dropping the guard did exactly that.
    """
    assert names.spent_by_the_dbt_sources() == {
        names.Name("DATABASE", "SUBEVENTS"),
        names.Name("SCHEMA", "RAW"),
        names.Name("TABLE", "RAW_SUBSCRIPTION_EVENTS"),
    }


def test_the_sources_reader_does_not_read_a_column_as_a_table():
    """It did, on the first run, and the other direction is what said so."""
    tables = {n.name for n in names.spent_by_the_dbt_sources() if n.kind == "TABLE"}
    assert tables == {"RAW_SUBSCRIPTION_EVENTS"}


def test_a_profile_value_that_is_an_env_var_is_not_a_name():
    """The warehouse target spells four of its settings as environment lookups.

    None of the four this reader wants is one of them, so the guard that skips
    a jinja lookup was never exercised and a mutation showed it.
    """
    profile = (
        "subevents:\n  outputs:\n    snowflake:\n"
        "      database: SUBEVENTS\n"
        "      schema: ANALYTICS\n"
        "      warehouse: \"{{ env_var('SUBEVENTS_WH') }}\"\n"
        "      role: DBT_TRANSFORMER_ROLE\n"
    )
    found = names.spent_by_the_dbt_profile(profile)
    assert names.Name("DATABASE", "SUBEVENTS") in found
    assert not [n for n in found if n.kind == "WAREHOUSE"]


def test_a_broken_reconciliation_fails_rather_than_passing():
    """Hand both directions an empty set and the rules have to notice."""
    saved = names.created
    try:
        names.created = lambda: set()
        findings = {f.rule: f.ok for f in names.run()}
    finally:
        names.created = saved
    assert findings["the_reconciliation_read_something"] is False
    assert findings["every_spent_name_is_created"] is False


def test_every_exception_carries_a_reason():
    for table in (names.SPENT_AND_NOT_CREATED, names.CREATED_AND_NOT_SPENT):
        for name, reason in table.items():
            assert len(reason) > 40, name


def test_every_relation_dbt_resolved_sits_in_a_schema_the_configuration_creates():
    """The parse is the only warehouse claim available with no account.

    It resolves a relation without connecting, so this cannot say the schema
    exists. What it says is that the name dbt will write to is a name the
    Terraform declares, which is the half that a typo breaks.
    """
    resolved = manifest()["resolved"]["snowflake"]
    assert resolved["exit_code"] == 0
    schemas = {n.name for n in names.created() if n.kind == "SCHEMA"}
    databases = {n.name for n in names.created() if n.kind == "DATABASE"}
    for relation in resolved["models"] + resolved["sources"]:
        database, schema, _ = relation.split(".")
        assert database in databases, relation
        assert schema in schemas, relation


def test_the_landing_table_is_the_one_the_sink_is_told_to_write_to():
    """One typo in the topic map and the models read an empty relation forever."""
    from_sink = {n for n in names.spent_by_the_sink() if n.kind == "TABLE"}
    from_sources = {n for n in names.spent_by_the_dbt_sources() if n.kind == "TABLE"}
    assert from_sink == from_sources
    assert from_sink == set(names.SPENT_AND_NOT_CREATED)


def test_a_profile_with_no_warehouse_target_raises():
    """Returning nothing would make three rules compare against an empty set.

    The guard was a silent fallback until a mutation showed that nothing
    graded it either way.
    """
    try:
        names.spent_by_the_dbt_profile("subevents:\n  target: duckdb\n  outputs:\n    duckdb:\n      type: duckdb\n")
    except ValueError as error:
        assert "snowflake" in str(error)
    else:
        raise AssertionError("a profile with no warehouse target must not read as empty")


def test_the_real_profile_does_declare_one():
    assert names.spent_by_the_dbt_profile()


def test_one_silent_reader_fails_the_reconciliation():
    """A total count would clear any floor with one of five readers silent."""
    saved = names.spent_by_the_sink
    try:
        names.spent_by_the_sink = lambda: set()
        finding = [
            f for f in names.run() if f.rule == "the_reconciliation_read_something"
        ][0]
    finally:
        names.spent_by_the_sink = saved
    assert finding.ok is False
    assert "snowflake-sink.json" in finding.detail
