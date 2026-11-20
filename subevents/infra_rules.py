"""Rules over the Terraform text, for the defects validate does not have.

`terraform validate` grades a configuration against the provider schema. Ten
defects were planted into this configuration on 2026-11-20 and it caught two of
them, a misspelled attribute and a required block that had been deleted. The
other eight were valid configurations that are wrong, and that gap is the whole
reason this module exists.

It reads the files as text. There is no HCL parser in the standard library and
adding one would put a dependency between a clone and its own tests, so these
are regular expressions over source. That is a real weakness and the answer to
it is `required_matches`. A rule whose pattern stops matching anything fails
rather than passing, because a rule that silently matches nothing reads exactly
like a rule that found nothing wrong.

Nothing here needs terraform on the machine, which is the point. The suite runs
on a clone with no cloud, no credentials and no provider binaries.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

INFRA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "infra")

# The gateway gives up on an integration at 30 seconds, so a function timeout at
# or above that is a limit the gateway will never let the function reach.
GATEWAY_INTEGRATION_CEILING = 30

# Variables that name something real in an account. A default on one of these
# is how a configuration gets applied somewhere nobody chose.
MUST_NOT_CARRY_A_DEFAULT = ("subnet_ids", "security_group_ids", "webhook_secret_arn")


@dataclass(frozen=True)
class Finding:
    rule: str
    ok: bool
    detail: str


def read(name: str) -> str:
    with open(os.path.join(INFRA_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _strip_comments(text: str) -> str:
    """Drop whole line comments so a rule cannot pass on prose about itself.

    This matters more than it looks. Half of these rules look for a literal and
    every one of those literals is also discussed in a comment above the line
    it governs.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def lambda_timeout_under_gateway_ceiling(files: dict[str, str]) -> Finding:
    found = re.findall(r"^\s*timeout\s*=\s*(\d+)\s*$", files["lambda.tf"], re.M)
    if not found:
        return Finding("lambda_timeout_under_gateway_ceiling", False, "no timeout is set")
    worst = max(int(value) for value in found)
    return Finding(
        "lambda_timeout_under_gateway_ceiling",
        worst < GATEWAY_INTEGRATION_CEILING,
        f"highest function timeout is {worst}s against a {GATEWAY_INTEGRATION_CEILING}s ceiling",
    )


def permission_is_scoped_to_this_api(files: dict[str, str]) -> Finding:
    block = _block(files["lambda.tf"], "aws_lambda_permission")
    scoped = bool(re.search(r"source_arn\s*=.*execution_arn", block))
    return Finding(
        "permission_is_scoped_to_this_api",
        scoped,
        "source_arn references the api execution arn" if scoped else "no source_arn on the permission",
    )


def route_accepts_an_unsigned_caller(files: dict[str, str]) -> Finding:
    """Stripe cannot sign with SigV4, so the route has to be open at the edge.

    This rule asserts the uncomfortable thing rather than a safe one. Somebody
    hardening this later will reach for AWS_IAM on the route and it will stop
    every delivery, so the reason is pinned here where a change has to argue
    with it.
    """
    block = _block(files["apigateway.tf"], "aws_apigatewayv2_route")
    open_route = 'authorization_type = "NONE"' in block
    return Finding(
        "route_accepts_an_unsigned_caller",
        open_route,
        "route authorization is NONE and the signature is the gate"
        if open_route
        else "route authorization is not NONE and Stripe cannot satisfy it",
    )


def no_wildcard_resource_in_a_role_policy(files: dict[str, str]) -> Finding:
    offenders = re.findall(r'Resource\s*=\s*"\*"', _strip_comments(files["iam.tf"]))
    return Finding(
        "no_wildcard_resource_in_a_role_policy",
        not offenders,
        f"{len(offenders)} policy statements grant every resource",
    )


def schema_compatibility_is_enforced(files: dict[str, str]) -> Finding:
    found = re.search(r'compatibility\s*=\s*"([A-Z_]+)"', _strip_comments(files["glue.tf"]))
    if not found:
        return Finding("schema_compatibility_is_enforced", False, "no compatibility is set")
    mode = found.group(1)
    return Finding(
        "schema_compatibility_is_enforced",
        mode != "NONE",
        f"registry compatibility is {mode}",
    )


def every_log_group_sets_retention(files: dict[str, str]) -> Finding:
    groups = 0
    with_retention = 0
    for text in files.values():
        for block in _blocks(text, "aws_cloudwatch_log_group"):
            groups += 1
            if re.search(r"retention_in_days\s*=", block):
                with_retention += 1
    return Finding(
        "every_log_group_sets_retention",
        groups > 0 and groups == with_retention,
        f"{with_retention} of {groups} log groups set a retention",
    )


def payload_format_matches_the_decoder(files: dict[str, str]) -> Finding:
    found = re.search(
        r'payload_format_version\s*=\s*"([\d.]+)"', _strip_comments(files["apigateway.tf"])
    )
    if not found:
        return Finding(
            "payload_format_matches_the_decoder", False, "no payload format version is set"
        )
    version = found.group(1)
    return Finding(
        "payload_format_matches_the_decoder",
        version == "2.0",
        f"integration sends payload format {version} and lambda_entry decodes 2.0",
    )


def cluster_requires_iam_authentication(files: dict[str, str]) -> Finding:
    text = _strip_comments(files["msk.tf"])
    enabled = bool(re.search(r"iam\s*\{\s*enabled\s*=\s*true", text))
    return Finding(
        "cluster_requires_iam_authentication",
        enabled,
        "sasl iam is enabled" if enabled else "the cluster does not require iam authentication",
    )


def no_account_shaped_variable_carries_a_default(files: dict[str, str]) -> Finding:
    offenders = []
    for name in MUST_NOT_CARRY_A_DEFAULT:
        block = _named_block(files["variables.tf"], "variable", name)
        if not block:
            offenders.append(f"{name} is not declared")
        elif re.search(r"^\s*default\s*=", block, re.M):
            offenders.append(f"{name} carries a default")
    return Finding(
        "no_account_shaped_variable_carries_a_default",
        not offenders,
        ", ".join(offenders) if offenders else f"{len(MUST_NOT_CARRY_A_DEFAULT)} variables stay unset",
    )


def default_endpoint_is_disabled(files: dict[str, str]) -> Finding:
    block = _block(files["apigateway.tf"], "aws_apigatewayv2_api")
    off = bool(re.search(r"disable_execute_api_endpoint\s*=\s*true", _strip_comments(block)))
    return Finding(
        "default_endpoint_is_disabled",
        off,
        "the execute-api hostname is off" if off else "the default gateway endpoint is reachable",
    )


RULES = (
    lambda_timeout_under_gateway_ceiling,
    permission_is_scoped_to_this_api,
    route_accepts_an_unsigned_caller,
    no_wildcard_resource_in_a_role_policy,
    schema_compatibility_is_enforced,
    every_log_group_sets_retention,
    payload_format_matches_the_decoder,
    cluster_requires_iam_authentication,
    no_account_shaped_variable_carries_a_default,
    default_endpoint_is_disabled,
)

FILES = (
    "apigateway.tf",
    "glue.tf",
    "iam.tf",
    "lambda.tf",
    "msk.tf",
    "outputs.tf",
    "variables.tf",
    "versions.tf",
)


def load() -> dict[str, str]:
    return {name: read(name) for name in FILES}


def run(files: dict[str, str] | None = None) -> list[Finding]:
    return [rule(files if files is not None else load()) for rule in RULES]


def _blocks(text: str, resource_type: str) -> list[str]:
    """Every block of one resource type, by brace depth from its header.

    Depth counting rather than a non greedy match to the next closing brace,
    because every one of these resources has nested blocks and the lazy version
    stops at the first inner one.
    """
    out = []
    for match in re.finditer(rf'resource\s+"{re.escape(resource_type)}"\s+"[^"]+"\s*\{{', text):
        out.append(_body_from(text, match.end() - 1))
    return out


def _block(text: str, resource_type: str) -> str:
    found = _blocks(text, resource_type)
    return found[0] if found else ""


def _named_block(text: str, kind: str, name: str) -> str:
    match = re.search(rf'{re.escape(kind)}\s+"{re.escape(name)}"\s*\{{', text)
    return _body_from(text, match.end() - 1) if match else ""


def _body_from(text: str, open_brace: int) -> str:
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : index + 1]
    return text[open_brace:]


# One defect per rule, planted into one file. Every planted configuration is
# still valid HCL. These live here rather than in the suite because a rule and
# the defect it has to refuse are one thing, and because scripts/validate_infra.py
# needs them too. A script under scripts/ importing a test module is what the
# dependency check caught on 2026-11-20, and it was right to.
PLANTED = {
    "lambda_timeout_under_gateway_ceiling": (
        "lambda.tf",
        lambda t: t.replace("timeout     = 29", "timeout     = 60"),
    ),
    "permission_is_scoped_to_this_api": (
        "lambda.tf",
        lambda t: re.sub(r"\n\s*source_arn = [^\n]*", "", t),
    ),
    "route_accepts_an_unsigned_caller": (
        "apigateway.tf",
        lambda t: t.replace('authorization_type = "NONE"', 'authorization_type = "AWS_IAM"'),
    ),
    "no_wildcard_resource_in_a_role_policy": (
        "iam.tf",
        lambda t: t.replace(
            "Resource = aws_msk_serverless_cluster.events.arn", 'Resource = "*"'
        ),
    ),
    "schema_compatibility_is_enforced": (
        "glue.tf",
        lambda t: t.replace('compatibility = "BACKWARD"', 'compatibility = "NONE"'),
    ),
    "every_log_group_sets_retention": (
        "lambda.tf",
        lambda t: re.sub(r"\n\s*retention_in_days = [^\n]*", "", t),
    ),
    "payload_format_matches_the_decoder": (
        "apigateway.tf",
        lambda t: t.replace('payload_format_version = "2.0"', 'payload_format_version = "1.0"'),
    ),
    "cluster_requires_iam_authentication": (
        "msk.tf",
        lambda t: re.sub(r"  client_authentication \{.*?\n  \}\n", "", t, flags=re.S),
    ),
    "no_account_shaped_variable_carries_a_default": (
        "variables.tf",
        lambda t: t.replace(
            'variable "security_group_ids" {\n  description = "Security groups for the cluster endpoints."\n  type        = list(string)\n}',
            'variable "security_group_ids" {\n  description = "Security groups for the cluster endpoints."\n  type        = list(string)\n  default     = ["sg-real"]\n}',
        ),
    ),
    "default_endpoint_is_disabled": (
        "apigateway.tf",
        lambda t: t.replace(
            "disable_execute_api_endpoint = true", "disable_execute_api_endpoint = false"
        ),
    ),
}
