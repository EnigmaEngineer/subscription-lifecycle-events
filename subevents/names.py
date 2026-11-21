"""Reconcile the warehouse names four files spend against the one that creates them.

One tool creates these objects and three others spend them. The Terraform
module declares a database and three schemas. It also declares a warehouse,
two roles and a service user. The Kafka sink names a database and a schema,
plus a role and a table. The dbt profile names a database and a schema, plus a
warehouse and a role. The dbt sources file names a database, a schema and a
table.

Nothing reads more than one of those files. `terraform validate` grades the
configuration against the provider schema and never opens the sink config.
`dbt parse` resolves a target without connecting, so a warehouse that does not
exist parses exactly like one that does. Neither tool is broken. The join is
simply nobody's job, so it is this module's.

The reconciliation runs in both directions, because a name spent and never
created and an object created and never spent are the same typo seen from
either end. Both directions have a declared list of deliberate exceptions with
a reason each, and a name in one of those lists that does not actually have
that status fails too.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from subevents.infra_rules import Finding
from subevents.warehouse_rules import blocks, strip_comments

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAREHOUSE_DIR = os.path.join(ROOT, "infra-snowflake")
CONNECTOR_CONFIG = os.path.join(ROOT, "connector", "snowflake-sink.json")
DBT_PROFILES = os.path.join(ROOT, "dbt", "profiles.yml")
DBT_PROJECT = os.path.join(ROOT, "dbt", "dbt_project.yml")
DBT_SOURCES = os.path.join(ROOT, "dbt", "models", "staging", "_sources.yml")

RESOURCE_KINDS = {
    "snowflake_database": "DATABASE",
    "snowflake_schema": "SCHEMA",
    "snowflake_warehouse": "WAREHOUSE",
    "snowflake_account_role": "ROLE",
    "snowflake_service_user": "USER",
}


@dataclass(frozen=True)
class Name:
    kind: str
    name: str

    def __str__(self) -> str:
        return f"{self.kind} {self.name}"


# Names something outside this configuration creates. Each one needs a reason
# and the reason has to be about ownership rather than about effort.
SPENT_AND_NOT_CREATED = {
    Name("TABLE", "RAW_SUBSCRIPTION_EVENTS"): (
        "The sink creates its target table on the first delivery and becomes its "
        "owner. Declaring it here would be two owners for one object, and the "
        "vendor recommends letting the sink create it to avoid a schema mismatch."
    ),
}

# Objects this configuration creates that no file here names. This one is the
# interesting direction. Externalising a secret also externalises the
# identifier beside it, so the one name that cannot be reconciled is the one
# the sink authenticates as.
CREATED_AND_NOT_SPENT = {
    Name("USER", "KAFKA_CONNECTOR_USER"): (
        "The sink reads snowflake.user.name from a Connect config provider, so "
        "the user name is in a secret store and not in any file here. Nothing "
        "compares the two and nothing can until the deployment exists."
    ),
}


def created() -> set[Name]:
    """Every object the warehouse module declares, by kind and name."""
    out: set[Name] = set()
    for entry in sorted(os.listdir(WAREHOUSE_DIR)):
        if not entry.endswith(".tf"):
            continue
        with open(os.path.join(WAREHOUSE_DIR, entry), encoding="utf-8") as handle:
            text = strip_comments(handle.read())
        for block in blocks(text):
            kind = RESOURCE_KINDS.get(block.kind)
            if kind is None:
                continue
            match = re.search(r'^\s*name\s*=\s*"([^"]+)"', block.body, re.M)
            if match:
                out.add(Name(kind, match.group(1).upper()))
    return out


def spent_by_the_sink() -> set[Name]:
    with open(CONNECTOR_CONFIG, encoding="utf-8") as handle:
        cfg = json.load(handle)["config"]
    out: set[Name] = set()
    for key, kind in (
        ("snowflake.database.name", "DATABASE"),
        ("snowflake.schema.name", "SCHEMA"),
        ("snowflake.role.name", "ROLE"),
    ):
        value = cfg.get(key)
        if value:
            out.add(Name(kind, value.upper()))
    for pair in cfg.get("snowflake.topic2table.map", "").split(","):
        if ":" in pair:
            out.add(Name("TABLE", pair.split(":", 1)[1].strip().upper()))
    return out


def _yaml_scalar(text: str, key: str) -> str | None:
    """Read one `key: value` from a block of yaml, without a yaml parser.

    These are four files this repository owns and the values are scalars on one
    line. A parser would be a dependency between a clone and its own tests, and
    the rule that catches a mangled read is that every name this returns has to
    reconcile against the other direction.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", text, re.M)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def spent_by_the_dbt_profile(text: str | None = None) -> set[Name]:
    """The names the warehouse target spends.

    A profile with no warehouse target raises rather than returning nothing.
    An empty answer here would make three of the four rules below compare
    against an empty set and report no problem, which is the 2026-10-26 shape
    of a gate that only runs where the answer is already true.
    """
    if text is None:
        with open(DBT_PROFILES, encoding="utf-8") as handle:
            text = handle.read()
    start = text.find("snowflake:")
    if start == -1:
        raise ValueError("the dbt profile declares no snowflake target")
    block = text[start:]
    out: set[Name] = set()
    for key, kind in (
        ("database", "DATABASE"),
        ("schema", "SCHEMA"),
        ("warehouse", "WAREHOUSE"),
        ("role", "ROLE"),
    ):
        value = _yaml_scalar(block, key)
        if value and "env_var" not in value:
            out.add(Name(kind, value.upper()))
    return out


def spent_by_the_dbt_project() -> set[Name]:
    """Every schema the models are configured to build into.

    These are the names the `generate_schema_name` override turns into whole
    schema names. Before that override they were suffixes on the profile's
    schema and resolved to names nothing creates.
    """
    with open(DBT_PROJECT, encoding="utf-8") as handle:
        text = handle.read()
    found = re.findall(r"^\s*\+schema:\s*([A-Z_]+)\s*$", text, re.M)
    return {Name("SCHEMA", value) for value in found}


def spent_by_the_dbt_sources() -> set[Name]:
    with open(DBT_SOURCES, encoding="utf-8") as handle:
        text = handle.read()
    out: set[Name] = set()
    # The database and schema are a jinja conditional per target, so the
    # warehouse name is the quoted upper case literal inside it.
    for key, kind in (("database", "DATABASE"), ("schema", "SCHEMA")):
        line = re.search(rf"^\s*{key}:\s*(.+)$", text, re.M)
        if not line:
            continue
        literal = re.search(r"'([A-Z][A-Z_]*)'", line.group(1))
        if literal:
            out.add(Name(kind, literal.group(1)))
    tables = re.search(r"tables:\n(.*)$", text, re.S)
    if tables:
        entries = re.findall(r"^(\s*)-\s*name:\s*(\S+)", tables.group(1), re.M)
        # Column names sit under a table entry as the same `- name:` shape at a
        # deeper indent. Taking every match read two columns as two tables, and
        # the other direction of the reconciliation is what said so. Only the
        # shallowest indentation is a table.
        if entries:
            top = min(len(indent) for indent, _ in entries)
            for indent, name in entries:
                if len(indent) == top:
                    out.add(Name("TABLE", name.upper()))
    return out


def spent() -> dict[Name, list[str]]:
    """Every name spent, carrying which files spend it."""
    sources = {
        "connector/snowflake-sink.json": spent_by_the_sink(),
        "dbt/profiles.yml": spent_by_the_dbt_profile(),
        "dbt/dbt_project.yml": spent_by_the_dbt_project(),
        "dbt/models/staging/_sources.yml": spent_by_the_dbt_sources(),
    }
    out: dict[Name, list[str]] = {}
    for origin, names in sources.items():
        for name in names:
            out.setdefault(name, []).append(origin)
    return out


def every_spent_name_is_created() -> Finding:
    unknown = sorted(
        str(name)
        for name in spent()
        if name not in created() and name not in SPENT_AND_NOT_CREATED
    )
    return Finding(
        "every_spent_name_is_created",
        not unknown,
        f"spent and never created: {unknown}" if unknown else "every spent name is declared",
    )


def every_created_name_is_spent() -> Finding:
    idle = sorted(
        str(name)
        for name in created()
        if name not in spent() and name not in CREATED_AND_NOT_SPENT
    )
    return Finding(
        "every_created_name_is_spent",
        not idle,
        f"created and never spent: {idle}" if idle else "every declared name is spent",
    )


def every_declared_exception_is_real() -> Finding:
    """An exception list that goes stale is worse than no exception list.

    A name listed as created elsewhere that this configuration now creates, or
    a name listed as unspent that something now spends, is a comment asserting
    something false about the files beside it.
    """
    made = created()
    used = spent()
    wrong = [
        f"{name} is declared as created elsewhere and is created here"
        for name in SPENT_AND_NOT_CREATED
        if name in made
    ]
    wrong += [
        f"{name} is declared as unspent and is spent by {used[name]}"
        for name in CREATED_AND_NOT_SPENT
        if name in used
    ]
    wrong += [
        f"{name} is declared as created elsewhere and nothing spends it"
        for name in SPENT_AND_NOT_CREATED
        if name not in used
    ]
    return Finding(
        "every_declared_exception_is_real",
        not wrong,
        "; ".join(wrong) if wrong else "both exception lists match the files",
    )


def the_reconciliation_read_something() -> Finding:
    """A comparison over two empty sets reports no problems.

    Every rule above is a set difference, and a set difference of nothing
    against nothing is empty. So this asks each reader separately rather than
    counting the total. A total would clear any floor with one reader silent,
    and one silent reader is what a changed file looks like.
    """
    readers = {
        "infra-snowflake": created(),
        "connector/snowflake-sink.json": spent_by_the_sink(),
        "dbt/profiles.yml": spent_by_the_dbt_profile(),
        "dbt/dbt_project.yml": spent_by_the_dbt_project(),
        "dbt/models/staging/_sources.yml": spent_by_the_dbt_sources(),
    }
    silent = sorted(origin for origin, found in readers.items() if not found)
    return Finding(
        "the_reconciliation_read_something",
        not silent,
        f"read nothing from {silent}" if silent else f"{len(readers)} readers all found names",
    )


RULES = (
    the_reconciliation_read_something,
    every_spent_name_is_created,
    every_created_name_is_spent,
    every_declared_exception_is_real,
)


def run() -> list[Finding]:
    return [rule() for rule in RULES]
