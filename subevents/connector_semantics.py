"""What the Snowflake Kafka connector does when a property is left out.

Most of what decides whether this sink behaves the way the platform needs is a
property with a default. Leaving one out is a choice and the choice is the
vendor's. So every default that matters is collected here with the sentence it
came from and the page it is on, and a rule cannot rest on a default nobody
read.

One property's default is documented twice with two different values, on two
pages of the same documentation set. That is not an error in this table. It is
the reason the table records a default per page rather than one default per
property.

Read 2026-11-21 from the pages named in SOURCES.
"""

from __future__ import annotations

from dataclasses import dataclass

INSTALL = "https://docs.snowflake.com/en/user-guide/kafka-connector-install"
STREAMING = (
    "https://docs.snowflake.com/en/user-guide/snowpipe-streaming/"
    "snowpipe-streaming-classic-kafka"
)
SCHEMATIZATION = (
    "https://docs.snowflake.com/en/user-guide/snowpipe-streaming/"
    "snowpipe-streaming-classic-kafka-schema-detection"
)
OVERVIEW = "https://docs.snowflake.com/en/user-guide/kafka-connector-overview"

SOURCES = (INSTALL, STREAMING, SCHEMATIZATION, OVERVIEW)


@dataclass(frozen=True)
class Source:
    """One page's statement of one property's default."""

    default: str
    quote: str
    url: str


# Keyed by connector property. The value is every statement of a default this
# table has read for it, and a property legitimately has more than one.
#
# A quote has to contain the default value it is evidence for. That is what
# `test_every_quote_contains_the_default_it_states` enforces and it is the
# reason the interesting sentences about a property live in RULINGS instead.
# A quote chosen for being interesting is decoration beside a number nobody
# can check.
DEFAULTS: dict[str, tuple[Source, ...]] = {
    "snowflake.ingestion.method": (
        Source("SNOWPIPE", "SNOWPIPE (default)", STREAMING),
    ),
    "snowflake.streaming.enable.single.buffer": (
        Source(
            "true",
            "Default : true for version 3.0.0 and later, false otherwise",
            STREAMING,
        ),
        Source(
            "false",
            "Default : true for version 3.0.0 and later, false otherwise",
            STREAMING,
        ),
    ),
    "buffer.flush.time": (
        Source(
            "10",
            "Values : Minimum: 1 Maximum: No upper limit Default : 10",
            STREAMING,
        ),
        Source(
            "120",
            "the flush is from the Kafka memory cache to the internal stage. "
            "The default value is 120 seconds.",
            INSTALL,
        ),
    ),
    "buffer.count.records": (
        Source(
            "10000",
            "Values : Minimum: 1 Maximum: No upper limit Default : 10000",
            STREAMING,
        ),
        Source("10000", "The default value is 10000 records.", INSTALL),
    ),
    "buffer.size.bytes": (
        Source("20000000", "Default : 20000000 (20 MB)", STREAMING),
        Source("5000000", "The default value for this is 5000000 (5 MB).", INSTALL),
    ),
    "snowflake.streaming.max.client.lag": (
        Source(
            "30",
            "Default : 30 seconds for version 3.1.1 and later, 120 seconds for "
            "versions 3.0.0 and 3.1.0, 1 second otherwise",
            STREAMING,
        ),
        Source(
            "120",
            "Default : 30 seconds for version 3.1.1 and later, 120 seconds for "
            "versions 3.0.0 and 3.1.0, 1 second otherwise",
            STREAMING,
        ),
        Source(
            "1",
            "Default : 30 seconds for version 3.1.1 and later, 120 seconds for "
            "versions 3.0.0 and 3.1.0, 1 second otherwise",
            STREAMING,
        ),
    ),
    "enable.streaming.client.optimization": (
        Source("true", "Values : true false Default : true", STREAMING),
    ),
    "errors.tolerance": (
        Source(
            "NONE",
            "NONE : Stop loading data when the first error is encountered.",
            STREAMING,
        ),
    ),
    "errors.log.enable": (
        Source("FALSE", "FALSE : Do not write error messages.", STREAMING),
    ),
    "errors.deadletterqueue.topic.name": (
        Source("", "Values : Custom text string Default : None", STREAMING),
    ),
    "snowflake.enable.schematization": (
        Source(
            "FALSE",
            "Specify to TRUE to enable schema detection and evolution for Kafka "
            "Connector with Snowpipe Streaming. The default value is FALSE.",
            SCHEMATIZATION,
        ),
    ),
    "behavior.on.null.values": (
        Source("DEFAULT", "The default value is DEFAULT .", INSTALL),
    ),
    "snowflake.metadata.all": (
        Source("TRUE", "The default value is TRUE.", INSTALL),
    ),
    "value.converter.break.on.schema.registry.error": (
        Source("false", "The default value is false .", INSTALL),
    ),
    "enable.streaming.channel.offset.migration": (
        Source(
            "true",
            "Default : true for versions from 2.1.2 until 3.4.0, false for "
            "version 3.4.0 and later",
            INSTALL,
        ),
        Source(
            "false",
            "Default : true for versions from 2.1.2 until 3.4.0, false for "
            "version 3.4.0 and later",
            INSTALL,
        ),
    ),
    "snowflake.snowpipe.v2CleanerEnabled": (
        Source(
            "true",
            "Default : true for versions 2.3.0 and later, false for version 2.2.2",
            INSTALL,
        ),
        Source(
            "false",
            "Default : true for versions 2.3.0 and later, false for version 2.2.2",
            INSTALL,
        ),
    ),
}

# Sentences that are not about one property's default and that decide the
# design anyway. Each one is quoted rather than paraphrased, because the
# paraphrase is where the reasoning went wrong on 2026-11-14.
RULINGS: dict[str, Source] = {
    "grants_are_not_inherited": Source(
        "",
        "Note that any privileges must be granted directly to the role used by "
        "the connector. Grants cannot be inherited from the role hierarchy.",
        INSTALL,
    ),
    "the_connector_owns_the_table_it_creates": Source(
        "",
        "If the connector creates a new target table for records from the Kafka "
        "topic the default role for the user specified in the Kafka "
        "configuration file becomes the table owner",
        INSTALL,
    ),
    "snowflake_converters_are_not_supported": Source(
        "",
        "Snowflake converters are not supported with Snowpipe Streaming.",
        STREAMING,
    ),
    "null_records_are_dropped": Source(
        "",
        "Records sent to Kafka brokers must not be NULL because these records "
        "will be dropped by the Kafka connector resulting in missing offsets.",
        INSTALL,
    ),
    "one_channel_per_topic_partition": Source(
        "",
        "Only one channel with that name can ingest into the table at a time.",
        STREAMING,
    ),
    "two_variant_columns": Source(
        "",
        "every Snowflake table loaded by the Kafka connector has a schema "
        "consisting of two VARIANT columns",
        OVERVIEW,
    ),
}

# The Snowflake converter class names. Naming them here rather than in a rule
# because the rule is about the streaming ruling above and not about a list
# somebody keeps current.
SNOWFLAKE_CONVERTERS = (
    "com.snowflake.kafka.connector.records.SnowflakeJsonConverter",
    "com.snowflake.kafka.connector.records.SnowflakeAvroConverter",
    "com.snowflake.kafka.connector.records.SnowflakeAvroConverterWithoutSchemaRegistry",
)


def documented_defaults(key: str) -> tuple[Source, ...]:
    """Every documented default for one property.

    Raises on a property this table has never read a page about. A rule that
    wants to say what happens when a property is omitted has to go through
    here, so a rule resting on a remembered default cannot be written.
    """
    if key not in DEFAULTS:
        raise KeyError(f"no default was read for {key}")
    return DEFAULTS[key]


def conflicting_defaults() -> dict[str, tuple[str, ...]]:
    """Properties two pages give different defaults for.

    This is the one worth knowing about, because which default applies depends
    on which page the reader happened to open and neither page mentions the
    other.
    """
    out: dict[str, tuple[str, ...]] = {}
    for key, sources in DEFAULTS.items():
        per_page = {}
        for source in sources:
            per_page.setdefault(source.url, set()).add(source.default)
        if len({frozenset(values) for values in per_page.values()}) > 1:
            out[key] = tuple(sorted({source.default for source in sources}))
    return out


def version_dependent_defaults() -> dict[str, tuple[str, ...]]:
    """Properties one page gives more than one default for, by connector version.

    A different thing from the above and the same hazard. A configuration that
    leaves one of these out behaves according to which release the Connect
    cluster happens to be running.
    """
    out: dict[str, tuple[str, ...]] = {}
    for key, sources in DEFAULTS.items():
        per_page: dict[str, set[str]] = {}
        for source in sources:
            per_page.setdefault(source.url, set()).add(source.default)
        if any(len(values) > 1 for values in per_page.values()):
            out[key] = tuple(sorted({source.default for source in sources}))
    return out
