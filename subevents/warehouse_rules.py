"""Rules over the warehouse configuration, for what the provider calls valid.

`terraform validate` grades this module against the provider schema and says
nothing about any of the following. Every rule here is about an attribute the
provider marks optional, which means the default is a decision somebody made
by not writing a line.

Three of them came out of `terraform providers schema -json` for
snowflakedb/snowflake 2.23.0 rather than out of a guide. The grant resource has
a flag that decides whether the configuration is the source of truth for the
privileges on an object, and it is off unless the file says otherwise.

Two came out of the vendor's own connector page and are quoted in
`connector_semantics.RULINGS` rather than paraphrased here.

Text rules again, for the reason given in `infra_rules`. The suite runs on a
clone with no terraform and no account.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from subevents.infra_rules import Finding

WAREHOUSE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "infra-snowflake"
)

FILES = ("versions.tf", "variables.tf", "main.tf", "grants.tf", "outputs.tf")

CONNECTOR_ROLE_REF = "snowflake_account_role.connector.name"

GRANT_RESOURCE = "snowflake_grant_privileges_to_account_role"


@dataclass(frozen=True)
class Block:
    kind: str
    label: str
    body: str


def read_all() -> dict[str, str]:
    out = {}
    for name in FILES:
        with open(os.path.join(WAREHOUSE_DIR, name), encoding="utf-8") as handle:
            out[name] = handle.read()
    return out


def strip_comments(text: str) -> str:
    """Drop whole line comments.

    Every rule below looks for a literal that also appears in the comment
    explaining the line it governs, so without this a rule passes on prose
    about itself.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def blocks(text: str) -> list[Block]:
    """Every top level resource and variable block, by brace depth.

    A lazy match to the next closing brace stops at the first nested block, and
    most of what these rules read sits after one.
    """
    out: list[Block] = []
    pattern = re.compile(r'^(resource|variable|provider|output)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{', re.M)
    for match in pattern.finditer(text):
        depth = 0
        index = match.end() - 1
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        kind = match.group(2) if match.group(1) == "resource" else match.group(1)
        label = match.group(3) or match.group(2)
        out.append(Block(kind, label, text[match.end() : index]))
    return out


def every_grant_is_a_source_of_truth(parts: dict[str, str]) -> Finding:
    """A grant left at the default ignores privileges nobody wrote down.

    The provider description says a resource with the flag off is only
    concerned with the privileges the configuration names. So a privilege
    granted by hand survives every apply and this file still reads like the
    whole picture.
    """
    grants = [b for b in blocks(strip_comments(parts["grants.tf"])) if b.kind == GRANT_RESOURCE]
    loose = [
        b.label for b in grants if not re.search(r"strict_privilege_management\s*=\s*true", b.body)
    ]
    return Finding(
        "every_grant_is_a_source_of_truth",
        bool(grants) and not loose,
        f"{len(grants)} privilege grants, not strict: {loose}",
    )


def no_grant_takes_all_privileges(parts: dict[str, str]) -> Finding:
    hits = [name for name, text in parts.items() if "all_privileges" in strip_comments(text)]
    return Finding(
        "no_grant_takes_all_privileges",
        not hits,
        f"all_privileges appears in {hits}" if hits else "no grant uses all_privileges",
    )


def no_grant_replans_on_every_apply(parts: dict[str, str]) -> Finding:
    """always_apply makes a plan that never comes back empty.

    The provider's own description says the flag is not compliant with the
    assumption that a configuration converges. A repository that cannot tell a
    real change from this one has no drift signal left.
    """
    hits = re.findall(r"always_apply\s*=\s*true", strip_comments(parts["grants.tf"]))
    return Finding(
        "no_grant_replans_on_every_apply",
        not hits,
        f"{len(hits)} grants set always_apply",
    )


def the_connector_role_is_granted_straight_to_the_user(parts: dict[str, str]) -> Finding:
    """The vendor page says these privileges cannot be inherited.

    So a role hierarchy carrying them lands nothing. The grant has to name the
    user, and nothing may hand the connector role a parent and rely on it.
    """
    text = strip_comments(parts["grants.tf"])
    direct = [
        b
        for b in blocks(text)
        if b.kind == "snowflake_grant_account_role"
        and CONNECTOR_ROLE_REF in b.body
        and "user_name" in b.body
    ]
    inherited = [
        b
        for b in blocks(text)
        if b.kind == "snowflake_grant_account_role"
        and CONNECTOR_ROLE_REF in b.body
        and "parent_role_name" in b.body
    ]
    return Finding(
        "the_connector_role_is_granted_straight_to_the_user",
        bool(direct) and not inherited,
        f"{len(direct)} direct grants and {len(inherited)} through a parent role",
    )


def the_service_user_declares_a_default_role(parts: dict[str, str]) -> Finding:
    """The sink creates its table and the user's default role owns it.

    That is the vendor's sentence rather than a reading of one. A user with no
    default role declared leaves the landing table owned by whatever the
    account hands out.
    """
    users = [
        b for b in blocks(strip_comments(parts["main.tf"])) if b.kind == "snowflake_service_user"
    ]
    missing = [b.label for b in users if "default_role" not in b.body]
    return Finding(
        "the_service_user_declares_a_default_role",
        bool(users) and not missing,
        f"{len(users)} service users, no default role on {missing}",
    )


def the_service_user_carries_a_public_key(parts: dict[str, str]) -> Finding:
    """rsa_public_key is optional, and a service user without one uses a password.

    Key pair authentication is the whole reason the connector takes a private
    key from a secret store. A user created without a public key falls back to
    the thing that was being avoided and no gate says so.
    """
    users = [
        b for b in blocks(strip_comments(parts["main.tf"])) if b.kind == "snowflake_service_user"
    ]
    missing = [b.label for b in users if "rsa_public_key" not in b.body]
    return Finding(
        "the_service_user_carries_a_public_key",
        bool(users) and not missing,
        f"{len(users)} service users, no public key on {missing}",
    )


def no_variable_carries_a_default(parts: dict[str, str]) -> Finding:
    """Every variable here names something real in an account."""
    variables = [b for b in blocks(strip_comments(parts["variables.tf"])) if b.kind == "variable"]
    defaulted = [b.label for b in variables if re.search(r"^\s*default\s*=", b.body, re.M)]
    return Finding(
        "no_variable_carries_a_default",
        bool(variables) and not defaulted,
        f"{len(variables)} variables, defaults on {defaulted}",
    )


def the_landing_table_is_not_declared(parts: dict[str, str]) -> Finding:
    """Two owners for one object is the defect this rule exists to refuse.

    The sink creates the target table on its first delivery and becomes its
    owner. A configuration declaring the same table would fight it on every
    apply, and the vendor recommends letting the sink create it anyway.
    """
    tables = [
        b.label
        for name, text in parts.items()
        for b in blocks(strip_comments(text))
        if b.kind == "snowflake_table"
    ]
    return Finding(
        "the_landing_table_is_not_declared",
        not tables,
        f"declared tables {tables}" if tables else "no table is declared here",
    )


RULES = (
    every_grant_is_a_source_of_truth,
    no_grant_takes_all_privileges,
    no_grant_replans_on_every_apply,
    the_connector_role_is_granted_straight_to_the_user,
    the_service_user_declares_a_default_role,
    the_service_user_carries_a_public_key,
    no_variable_carries_a_default,
    the_landing_table_is_not_declared,
)


def run(parts: dict[str, str] | None = None) -> list[Finding]:
    files = read_all() if parts is None else parts
    return [rule(files) for rule in RULES]


# One defect per rule, planted into one file, every one of them still valid HCL.
PLANTED = {
    "every_grant_is_a_source_of_truth": (
        "grants.tf",
        lambda t: t.replace(
            "  strict_privilege_management = true\n\n  on_account_object {\n    object_type = \"WAREHOUSE\"",
            "\n  on_account_object {\n    object_type = \"WAREHOUSE\"",
        ),
    ),
    "no_grant_takes_all_privileges": (
        "grants.tf",
        lambda t: t.replace('privileges                  = ["SELECT"]', "all_privileges              = true"),
    ),
    "no_grant_replans_on_every_apply": (
        "grants.tf",
        lambda t: t.replace(
            "  strict_privilege_management = true\n\n  on_schema_object {",
            "  strict_privilege_management = true\n  always_apply                = true\n\n  on_schema_object {",
        ),
    ),
    "the_connector_role_is_granted_straight_to_the_user": (
        "grants.tf",
        lambda t: t.replace(
            "  user_name = snowflake_service_user.connector.name",
            '  parent_role_name = "SYSADMIN"',
        ),
    ),
    "the_service_user_declares_a_default_role": (
        "main.tf",
        lambda t: re.sub(r"\n\s*default_role = [^\n]*", "", t),
    ),
    "the_service_user_carries_a_public_key": (
        "main.tf",
        lambda t: re.sub(r"\n\s*rsa_public_key = [^\n]*", "", t),
    ),
    "no_variable_carries_a_default": (
        "variables.tf",
        lambda t: t.replace(
            '  description = "Snowflake account name inside the organization."\n  type        = string',
            '  description = "Snowflake account name inside the organization."\n  type        = string\n  default     = "prod"',
        ),
    ),
    "the_landing_table_is_not_declared": (
        "main.tf",
        lambda t: t
        + '\nresource "snowflake_table" "landing" {\n  database = snowflake_database.subevents.name\n  schema   = snowflake_schema.raw.name\n  name     = "RAW_SUBSCRIPTION_EVENTS"\n\n  column {\n    name = "RECORD_CONTENT"\n    type = "VARIANT"\n  }\n}\n',
    ),
}
