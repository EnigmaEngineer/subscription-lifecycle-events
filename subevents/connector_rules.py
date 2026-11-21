"""Rules over the Kafka sink configuration, for the defects nothing else grades.

There is no validator for a Kafka Connect configuration that runs without a
Connect cluster. The REST endpoint has a validate call and reaching it needs a
running worker and a reachable Snowflake account, so on a machine with neither
the configuration is a JSON document that nobody checks.

Every rule here is about a property the connector will happily do without. The
defaults are in `connector_semantics` with the sentence each one came from, and
a rule that wants to say what an omission costs reads them from there rather
than from memory.

The configuration is JSON, so unlike the Terraform rules these read a parsed
document and not source text. Each rule still has a planted defect it has to
refuse, because a rule nobody has seen fail is a rule nobody has tested.
"""

from __future__ import annotations

import copy
import json
import os
import re

from subevents import connector_semantics as sem
from subevents.infra_rules import Finding

CONNECTOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "connector"
)
CONFIG_FILE = "snowflake-sink.json"

# Properties whose value is a credential or an endpoint identifying the account.
# Each one has to arrive through a Connect config provider. Snowflake's own page
# recommends it and the reason this repository enforces it is narrower: a key
# shaped string typed into a file in this repository is what opened three
# scanner alerts on 2026-11-18.
MUST_BE_EXTERNALISED = (
    "snowflake.url.name",
    "snowflake.user.name",
    "snowflake.private.key",
    "snowflake.private.key.passphrase",
)

# A Connect config provider reference. The worker resolves it at startup.
PROVIDER_REFERENCE = re.compile(r"^\$\{[a-zA-Z0-9_.-]+:[^}]+\}$")

# The documented range for the streaming flush interval. Both ends are graded
# in the suite at the boundary and one past it, because the committed value is
# nowhere near either of them and both bounds survived a mutation.
MAX_CLIENT_LAG_RANGE = (1, 600)

# Properties with more than one documented default that this configuration
# deliberately leaves alone, with the reason each one is safe to leave.
#
# An omission list needs its complement written down, so there is a second rule
# below asserting every entry here is really contested and really absent. A
# list of deliberate omissions that goes stale is a comment claiming something
# false about the file beside it.
DELIBERATELY_AT_THE_DEFAULT = {
    "buffer.flush.time": (
        "Single buffer is on, and the page says that makes this property "
        "irrelevant. Stating it would read as a tuned flush interval that does "
        "nothing at all."
    ),
    "enable.streaming.channel.offset.migration": (
        "Both documented values are safe for a deployment that has never run "
        "the two releases whose channel names it migrates from. The page also "
        "says to consult the vendor before disabling it."
    ),
    "snowflake.snowpipe.v2CleanerEnabled": (
        "It cleans stage files for the Snowpipe ingestion method and this "
        "deployment does not use that method."
    ),
}


def load_config(path: str | None = None) -> dict:
    target = path or os.path.join(CONNECTOR_DIR, CONFIG_FILE)
    with open(target, encoding="utf-8") as handle:
        return json.load(handle)["config"]


def ingestion_method_is_stated(cfg: dict) -> Finding:
    """The default is Snowpipe, so a streaming deployment says so or it is not one.

    This is the rule worth having first. Every other property in the file reads
    like a streaming configuration and one omitted line turns the whole thing
    into batch loading through an internal stage, with no error anywhere.
    """
    documented = sem.documented_defaults("snowflake.ingestion.method")[0].default
    value = cfg.get("snowflake.ingestion.method")
    return Finding(
        "ingestion_method_is_stated",
        value == "SNOWPIPE_STREAMING",
        f"method is {value!r} and the documented default is {documented}",
    )


def converter_is_supported_by_streaming(cfg: dict) -> Finding:
    """Snowflake's own converters do not work on the streaming path.

    The install page's sample configuration uses one of them. That sample is
    where a reader starts, and switching the ingestion method on the other page
    is what makes it wrong.
    """
    value = cfg.get("value.converter", "")
    offending = value in sem.SNOWFLAKE_CONVERTERS
    return Finding(
        "converter_is_supported_by_streaming",
        not offending and bool(value),
        f"value.converter is {value!r}",
    )


def tolerated_errors_have_somewhere_to_go(cfg: dict) -> Finding:
    """Tolerating every error with no queue behind it is silent loss.

    Three defaults compose into this one. Tolerance refuses by default, logging
    is off by default and the queue has no default name at all. Turning the
    first one on without the other two is the shape that loses records quietly.
    """
    tolerance = cfg.get("errors.tolerance", sem.documented_defaults("errors.tolerance")[0].default)
    queue = cfg.get("errors.deadletterqueue.topic.name", "")
    logging = str(cfg.get("errors.log.enable", "")).lower() == "true"
    if tolerance.upper() != "ALL":
        return Finding(
            "tolerated_errors_have_somewhere_to_go",
            True,
            f"tolerance is {tolerance} so a bad record stops the connector",
        )
    return Finding(
        "tolerated_errors_have_somewhere_to_go",
        bool(queue) and logging,
        f"tolerance is ALL with queue {queue!r} and logging {logging}",
    )


def dead_letter_records_carry_no_payload(cfg: dict) -> Finding:
    """A Stripe event body in the Connect log is a copy of customer data.

    The queue gets the record either way. What this refuses is the second copy
    in a log file with different retention and different readers.
    """
    include = str(cfg.get("errors.log.include.messages", "")).lower()
    return Finding(
        "dead_letter_records_carry_no_payload",
        include == "false",
        f"errors.log.include.messages is {include!r}",
    )


def secrets_arrive_through_a_config_provider(cfg: dict) -> Finding:
    """No credential or account endpoint is a literal in this file."""
    literals = [
        key
        for key in MUST_BE_EXTERNALISED
        if not PROVIDER_REFERENCE.match(str(cfg.get(key, "")))
    ]
    return Finding(
        "secrets_arrive_through_a_config_provider",
        not literals,
        f"literal values for {literals}" if literals else "all four are provider references",
    )


def the_ingesting_role_is_named(cfg: dict) -> Finding:
    """Without a role the connector uses whatever the user defaults to.

    That matters more here than it would elsewhere, because the connector
    creates the target table and the user's default role becomes its owner.
    """
    value = cfg.get("snowflake.role.name", "")
    return Finding(
        "the_ingesting_role_is_named",
        bool(value),
        f"snowflake.role.name is {value!r}",
    )


def no_buffer_setting_that_single_buffer_ignores(cfg: dict) -> Finding:
    """A tuning line that the documentation says is irrelevant is a lie in a file.

    Single buffer is on by default from version 3.0.0 and the page says it makes
    `buffer.flush.time` and `buffer.count.records` irrelevant. A configuration
    carrying both reads like somebody tuned the flush interval.
    """
    single = str(
        cfg.get(
            "snowflake.streaming.enable.single.buffer",
            sem.documented_defaults("snowflake.streaming.enable.single.buffer")[0].default,
        )
    ).lower()
    ignored = [key for key in ("buffer.flush.time", "buffer.count.records") if key in cfg]
    if single != "true":
        return Finding(
            "no_buffer_setting_that_single_buffer_ignores",
            True,
            "single buffer is off so the buffer settings do something",
        )
    return Finding(
        "no_buffer_setting_that_single_buffer_ignores",
        not ignored,
        f"single buffer is on and the file still sets {ignored}",
    )


def client_lag_is_inside_the_documented_range(cfg: dict) -> Finding:
    low, high = MAX_CLIENT_LAG_RANGE
    raw = cfg.get("snowflake.streaming.max.client.lag")
    if raw is None:
        default = sem.documented_defaults("snowflake.streaming.max.client.lag")[0].default
        return Finding(
            "client_lag_is_inside_the_documented_range",
            False,
            f"not set and the default moves by connector version, last documented at {default}",
        )
    value = int(raw)
    return Finding(
        "client_lag_is_inside_the_documented_range",
        low <= value <= high,
        f"max client lag is {value}s against a documented {low} to {high}",
    )


def schematization_off_means_two_variant_columns(cfg: dict) -> Finding:
    """Schema evolution on without a registry url is a table shape nobody chose.

    This platform wants the raw envelope in one column, because the staging
    model owns the unpacking and the suite grades that model. Turning
    schematization on moves the unpacking into the connector and needs a
    registry url, which this deployment does not run.
    """
    enabled = str(cfg.get("snowflake.enable.schematization", "false")).lower() == "true"
    registry = cfg.get("schema.registry.url", "")
    if not enabled:
        return Finding(
            "schematization_off_means_two_variant_columns",
            True,
            "schematization is off so the landing table is the two column shape",
        )
    return Finding(
        "schematization_off_means_two_variant_columns",
        bool(registry),
        f"schematization is on with registry url {registry!r}",
    )


def a_tombstone_does_not_become_a_row(cfg: dict) -> Finding:
    """The default inserts an empty JSON string rather than skipping the record.

    A row whose content is an empty object reaches the staging model with no
    event id in it. There is no Stripe event that looks like that, so the
    honest answer is to skip the record at the sink.
    """
    default = sem.documented_defaults("behavior.on.null.values")[0].default
    value = cfg.get("behavior.on.null.values", default)
    return Finding(
        "a_tombstone_does_not_become_a_row",
        value.upper() == "IGNORE",
        f"behavior.on.null.values is {value} and the documented default is {default}",
    )


def no_property_rests_on_a_contested_default(cfg: dict) -> Finding:
    """A property with more than one documented default is stated or left out.

    Six of the sixteen defaults read on 2026-11-21 are documented more than one
    way. Two of those differ between pages and four differ by connector
    version. A configuration that states some of them and leaves the rest to
    the default is resting on which page somebody opened or which release the
    cluster is running.

    Stating all of them or none of them are both defensible. Half is not, and
    half is what a file assembled from two pages ends up as.
    """
    contested = dict(sem.conflicting_defaults())
    contested.update(sem.version_dependent_defaults())
    silent = sorted(
        key
        for key in contested
        if key not in cfg and key not in DELIBERATELY_AT_THE_DEFAULT
    )
    return Finding(
        "no_property_rests_on_a_contested_default",
        not silent,
        f"{len(contested)} contested, undeclared omissions {silent}",
    )


def every_deliberate_omission_is_real(cfg: dict) -> Finding:
    """The complement of the rule above.

    An entry the configuration now states, or an entry whose default turned out
    to be documented one way after all, is a reason sitting beside nothing.
    """
    contested = dict(sem.conflicting_defaults())
    contested.update(sem.version_dependent_defaults())
    wrong = [
        f"{key} is declared as left at the default and the file states it"
        for key in DELIBERATELY_AT_THE_DEFAULT
        if key in cfg
    ]
    wrong += [
        f"{key} is declared as contested and has one documented default"
        for key in DELIBERATELY_AT_THE_DEFAULT
        if key not in contested
    ]
    wrong += [
        f"{key} has no reason and is not in the defaults table"
        for key in DELIBERATELY_AT_THE_DEFAULT
        if key not in sem.DEFAULTS
    ]
    return Finding(
        "every_deliberate_omission_is_real",
        not wrong,
        "; ".join(wrong) if wrong else f"{len(DELIBERATELY_AT_THE_DEFAULT)} omissions all real",
    )


RULES = (
    ingestion_method_is_stated,
    converter_is_supported_by_streaming,
    tolerated_errors_have_somewhere_to_go,
    dead_letter_records_carry_no_payload,
    secrets_arrive_through_a_config_provider,
    the_ingesting_role_is_named,
    no_buffer_setting_that_single_buffer_ignores,
    client_lag_is_inside_the_documented_range,
    schematization_off_means_two_variant_columns,
    a_tombstone_does_not_become_a_row,
    no_property_rests_on_a_contested_default,
    every_deliberate_omission_is_real,
)


def run(cfg: dict | None = None) -> list[Finding]:
    config = load_config() if cfg is None else cfg
    return [rule(config) for rule in RULES]


# One defect per rule. Every planted configuration is still valid JSON and
# every one of them is a configuration somebody would plausibly write. The
# first two are the ones a reader of the install page writes by accident.
PLANTED = {
    "ingestion_method_is_stated": lambda c: _without(c, "snowflake.ingestion.method"),
    "converter_is_supported_by_streaming": lambda c: _with(
        c, "value.converter", sem.SNOWFLAKE_CONVERTERS[0]
    ),
    "tolerated_errors_have_somewhere_to_go": lambda c: _without(
        c, "errors.deadletterqueue.topic.name"
    ),
    "dead_letter_records_carry_no_payload": lambda c: _with(
        c, "errors.log.include.messages", "true"
    ),
    "secrets_arrive_through_a_config_provider": lambda c: _with(
        c, "snowflake.user.name", "kafka_connector_user"
    ),
    "the_ingesting_role_is_named": lambda c: _without(c, "snowflake.role.name"),
    "no_buffer_setting_that_single_buffer_ignores": lambda c: _with(
        c, "buffer.flush.time", "1"
    ),
    "client_lag_is_inside_the_documented_range": lambda c: _with(
        c, "snowflake.streaming.max.client.lag", "900"
    ),
    "schematization_off_means_two_variant_columns": lambda c: _with(
        c, "snowflake.enable.schematization", "true"
    ),
    "a_tombstone_does_not_become_a_row": lambda c: _without(c, "behavior.on.null.values"),
    "no_property_rests_on_a_contested_default": lambda c: _without(c, "buffer.size.bytes"),
    "every_deliberate_omission_is_real": lambda c: _with(
        c, "snowflake.snowpipe.v2CleanerEnabled", "true"
    ),
}


def _with(cfg: dict, key: str, value: str) -> dict:
    out = copy.deepcopy(cfg)
    out[key] = value
    return out


def _without(cfg: dict, key: str) -> dict:
    out = copy.deepcopy(cfg)
    out.pop(key, None)
    return out
