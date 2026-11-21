"""The Kafka sink configuration, and the defaults it is not resting on.

Every rule is checked twice. Once against the committed configuration, where
it has to pass, and once against a configuration carrying the one defect it
exists to refuse. A rule nobody has seen fail is a rule nobody has tested, and
the Terraform work on 2026-11-20 is where that lesson came from.
"""

import json
import os

from subevents import connector_rules as rules
from subevents import connector_semantics as sem

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_configuration_is_valid_json_with_a_config_block():
    with open(rules.load_config.__module__ and os.path.join(rules.CONNECTOR_DIR, rules.CONFIG_FILE), encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["name"]
    assert payload["config"]["connector.class"].endswith("SnowflakeSinkConnector")


def test_every_rule_passes_on_the_committed_configuration():
    failures = {f.rule: f.detail for f in rules.run() if not f.ok}
    assert failures == {}


def test_every_rule_has_a_planted_defect():
    named = {rule.__name__ for rule in rules.RULES}
    assert named == set(rules.PLANTED)


def test_every_planted_defect_is_refused():
    base = rules.load_config()
    missed = {}
    for name, mutate in rules.PLANTED.items():
        finding = [f for f in rules.run(mutate(base)) if f.rule == name][0]
        if finding.ok:
            missed[name] = finding.detail
    assert missed == {}


def test_a_planted_defect_changes_only_its_own_rule():
    """A defect that trips two rules means one of them is not specific.

    Three of these legitimately overlap, because the error handling properties
    compose, so the assertion is on the rule that was aimed at rather than on
    the count.
    """
    base = rules.load_config()
    for name, mutate in rules.PLANTED.items():
        findings = {f.rule: f.ok for f in rules.run(mutate(base))}
        assert findings[name] is False, name


def test_every_default_in_the_table_carries_a_quote_and_a_url():
    for key, sources in sem.DEFAULTS.items():
        assert sources, key
        for source in sources:
            assert source.quote.strip(), key
            assert source.url in sem.SOURCES, key


def test_every_quote_contains_the_default_it_states():
    """A quote that does not carry the value is decoration beside a number.

    This is the check that keeps the table honest. The sentence a page uses to
    say what a default is has the default in it, so a quote without it was
    chosen for being interesting rather than for being evidence.
    """
    unsupported = [
        (key, source.default)
        for key, sources in sem.DEFAULTS.items()
        for source in sources
        if source.default and source.default not in source.quote
    ]
    assert unsupported == []


def test_every_ruling_carries_a_quote_and_a_url():
    for key, source in sem.RULINGS.items():
        assert len(source.quote) > 20, key
        assert source.url in sem.SOURCES, key


def test_a_default_nobody_read_raises_rather_than_guessing():
    try:
        sem.documented_defaults("snowflake.invented.property")
    except KeyError as error:
        assert "snowflake.invented.property" in str(error)
    else:
        raise AssertionError("a property with no page behind it must not resolve")


def test_the_pages_disagree_about_exactly_two_properties():
    """Two pages of one documentation set state different defaults for these.

    Pinned rather than counted, because the interesting part is which two.
    """
    assert sem.conflicting_defaults() == {
        "buffer.flush.time": ("10", "120"),
        "buffer.size.bytes": ("20000000", "5000000"),
    }


def test_four_defaults_depend_on_the_connector_version():
    assert set(sem.version_dependent_defaults()) == {
        "snowflake.streaming.enable.single.buffer",
        "snowflake.streaming.max.client.lag",
        "enable.streaming.channel.offset.migration",
        "snowflake.snowpipe.v2CleanerEnabled",
    }


def test_the_two_contested_sets_do_not_overlap():
    """They are different hazards and a property in both would be a read error."""
    assert not set(sem.conflicting_defaults()) & set(sem.version_dependent_defaults())


def test_no_snowflake_converter_is_named_in_the_configuration():
    """The streaming path does not support them and the install page's sample uses one."""
    text = json.dumps(rules.load_config())
    for converter in sem.SNOWFLAKE_CONVERTERS:
        assert converter not in text


def test_no_credential_shaped_string_is_in_the_connector_directory():
    """Covered by test_no_secrets over the whole repository and asserted here too.

    The sink configuration is the one file in this repository whose whole job
    is to carry connection settings, so it gets the check pointed at it
    directly rather than only as one file in a sweep.
    """
    with open(os.path.join(rules.CONNECTOR_DIR, rules.CONFIG_FILE), encoding="utf-8") as handle:
        text = handle.read()
    assert "BEGIN PRIVATE KEY" not in text
    assert "BEGIN ENCRYPTED PRIVATE KEY" not in text
    for key in rules.MUST_BE_EXTERNALISED:
        value = rules.load_config()[key]
        assert rules.PROVIDER_REFERENCE.match(value), key


def test_the_client_lag_range_is_graded_at_both_boundaries():
    """The committed value is 5, so neither end of the range was ever read.

    Both bounds survived a mutation. A range nothing is graded at is two
    numbers in a tuple.
    """
    base = rules.load_config()
    # The bounds are typed out rather than read off the constant. The first
    # version of this test took them from the module it is grading, so the
    # expectations moved with the thing they were supposed to pin and both
    # bounds survived the mutation a second time.
    #
    # The page says "Minimum: 1 second" and "Maximum: 600 seconds".
    assert rules.MAX_CLIENT_LAG_RANGE == (1, 600)
    for value, expected in ((0, False), (1, True), (600, True), (601, False)):
        cfg = dict(base)
        cfg["snowflake.streaming.max.client.lag"] = str(value)
        finding = [
            f for f in rules.run(cfg) if f.rule == "client_lag_is_inside_the_documented_range"
        ][0]
        assert finding.ok is expected, value


def test_refusing_every_error_needs_no_queue_behind_it():
    """The branch where tolerance is NONE had no test and survived a mutation.

    A connector that stops on the first bad record loses nothing, so the rule
    has nothing to complain about. That is the half of the rule the committed
    configuration never reaches.
    """
    cfg = rules.load_config()
    cfg["errors.tolerance"] = "NONE"
    cfg.pop("errors.deadletterqueue.topic.name")
    cfg["errors.log.enable"] = "false"
    finding = [
        f for f in rules.run(cfg) if f.rule == "tolerated_errors_have_somewhere_to_go"
    ][0]
    assert finding.ok is True
    assert "NONE" in finding.detail


def test_a_buffer_setting_is_fine_once_single_buffer_is_off():
    """The other side of the rule, and the committed file never reaches it."""
    cfg = rules.load_config()
    cfg["snowflake.streaming.enable.single.buffer"] = "false"
    cfg["buffer.flush.time"] = "30"
    finding = [
        f for f in rules.run(cfg) if f.rule == "no_buffer_setting_that_single_buffer_ignores"
    ][0]
    assert finding.ok is True


def test_an_absent_single_buffer_setting_is_read_as_the_documented_default():
    """The committed file states it, so the default path was never graded.

    It matters because the default flipped at version 3.0.0 and the table
    records both values. A rule reading the wrong one would call single buffer
    off and then say nothing about a flush interval that does nothing.
    """
    cfg = rules.load_config()
    cfg.pop("snowflake.streaming.enable.single.buffer")
    cfg["buffer.flush.time"] = "1"
    finding = [
        f for f in rules.run(cfg) if f.rule == "no_buffer_setting_that_single_buffer_ignores"
    ][0]
    assert finding.ok is False
    assert "buffer.flush.time" in finding.detail


def test_an_absent_client_lag_names_the_default_that_moves_by_version():
    """Three documented values for one property and the rule reports the first.

    The detail line is what a reader acts on, so it carries the figure rather
    than saying the property is unset.
    """
    cfg = rules.load_config()
    cfg.pop("snowflake.streaming.max.client.lag")
    finding = [
        f for f in rules.run(cfg) if f.rule == "client_lag_is_inside_the_documented_range"
    ][0]
    assert finding.ok is False
    assert "30" in finding.detail


def test_the_rule_refuses_every_snowflake_converter_and_not_just_the_first():
    """Three class names and one ruling, so the rule is graded on all three.

    The planted defect uses the first of them. Pointing it at the second
    survives a mutation, because every one of the three is refused the same
    way, and that is the argument for grading the list rather than the index.
    """
    base = rules.load_config()
    for converter in sem.SNOWFLAKE_CONVERTERS:
        cfg = dict(base)
        cfg["value.converter"] = converter
        finding = [
            f for f in rules.run(cfg) if f.rule == "converter_is_supported_by_streaming"
        ][0]
        assert finding.ok is False, converter


def test_an_empty_converter_is_refused_too():
    """A missing converter is not a Snowflake one and is still not usable."""
    cfg = rules.load_config()
    cfg.pop("value.converter")
    finding = [f for f in rules.run(cfg) if f.rule == "converter_is_supported_by_streaming"][0]
    assert finding.ok is False
