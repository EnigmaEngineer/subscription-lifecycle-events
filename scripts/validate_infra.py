"""Run terraform over the configuration and over ten broken copies of it.

This is the only script here that needs a tool the suite cannot assume. There
is no terraform on the machine the daily build runs on, so this runs where
terraform is and writes a manifest, and `tests/test_infra.py` reads the manifest
rather than shelling out. That split is what keeps the suite runnable on a clone
with nothing installed.

It refuses to write anything if terraform is missing. A manifest invented on a
machine with no terraform would be a measurement nobody made.

    python scripts/validate_infra.py --out measurements

The ten defects are the ten rules in subevents/infra_rules.py, planted one at a
time into a copy of infra/. The question the manifest answers is which of them
terraform accepts, because those are the ones the rules have to carry.

There is an eleventh case and it is not a rule. It misspells an attribute name,
which terraform has to refuse. It exists because a manifest reporting that
validate caught nothing reads the same whether validate is discerning or
whether it never ran, and the control is the only cheap way to tell those apart.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

INFRA = os.path.join(ROOT, "infra")


def plants() -> dict:
    """The plants that live beside the rules, so the two cannot drift apart."""
    from subevents import infra_rules

    return infra_rules.PLANTED


# Not a rule and not optional. If terraform accepts this, terraform is not
# grading the configuration and every other line of the manifest is worthless.
CONTROL = (
    "misspelled_attribute_must_be_refused",
    "lambda.tf",
    lambda t: t.replace("bootstrap_brokers_sasl_iam", "bootstrap_brokers_sasl_iam_typo"),
)


def terraform(*args, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["terraform", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def copy_infra(target: str) -> None:
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(INFRA):
        if name.endswith(".tf"):
            shutil.copy(os.path.join(INFRA, name), os.path.join(target, name))
    schemas = os.path.join(os.path.dirname(target), "schemas")
    os.makedirs(schemas, exist_ok=True)
    shutil.copy(
        os.path.join(ROOT, "schemas", "subscription-event.json"),
        os.path.join(schemas, "subscription-event.json"),
    )


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if shutil.which("terraform") is None:
        print("terraform is not on this machine, so nothing was measured", file=sys.stderr)
        raise SystemExit(2)

    out_dir = os.path.join(ROOT, "measurements")
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]

    version = terraform("version", "-json", cwd=ROOT).stdout
    tf_version = json.loads(version)["terraform_version"]

    work = tempfile.mkdtemp(prefix="subevents-infra-")
    base = os.path.join(work, "infra")
    copy_infra(base)

    fmt = terraform("fmt", "-check", "-diff", "-no-color", cwd=base)
    init = terraform("init", "-backend=false", "-no-color", cwd=base)
    if init.returncode != 0:
        print(init.stdout + init.stderr, file=sys.stderr)
        raise SystemExit(init.returncode)
    clean = terraform("validate", "-no-color", cwd=base)

    results = {}
    cases = sorted(plants().items())
    cases.append((CONTROL[0], (CONTROL[1], CONTROL[2])))
    for rule, (target, plant) in cases:
        case = os.path.join(work, "case-" + rule, "infra")
        copy_infra(case)
        shutil.copytree(
            os.path.join(base, ".terraform"), os.path.join(case, ".terraform"), dirs_exist_ok=True
        )
        shutil.copy(
            os.path.join(base, ".terraform.lock.hcl"), os.path.join(case, ".terraform.lock.hcl")
        )
        with open(os.path.join(case, target), encoding="utf-8") as handle:
            original = handle.read()
        broken = plant(original)
        if broken == original:
            raise SystemExit(f"the plant for {rule} changed nothing")
        with open(os.path.join(case, target), "w", encoding="utf-8") as handle:
            handle.write(broken)

        run = terraform("validate", "-no-color", cwd=case)
        caught = run.returncode != 0
        results[rule] = {
            "file": target,
            "caught_by_terraform": caught,
            "first_error": _first_error(run.stdout + run.stderr) if caught else "",
        }

    control = results.pop(CONTROL[0])
    if not control["caught_by_terraform"]:
        raise SystemExit(
            "terraform accepted a misspelled attribute, so it is not grading anything"
        )

    manifest = {
        "generated_by": "scripts/validate_infra.py",
        "control_refused": control["caught_by_terraform"],
        "control_error": control["first_error"],
        "terraform_version": tf_version,
        "fmt_clean": fmt.returncode == 0,
        "validate_clean": clean.returncode == 0,
        "validate_output": clean.stdout.strip(),
        "planted_defects": len(results),
        "caught_by_terraform": sum(1 for r in results.values() if r["caught_by_terraform"]),
        "missed_by_terraform": sum(1 for r in results.values() if not r["caught_by_terraform"]),
        "defects": results,
    }

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "terraform.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"terraform                 {tf_version}")
    print(f"fmt -check                {'clean' if manifest['fmt_clean'] else 'reformatted'}")
    print(f"validate                  {manifest['validate_output']}")
    print(f"planted defects           {manifest['planted_defects']}")
    print(f"caught by validate        {manifest['caught_by_terraform']}")
    print(f"missed by validate        {manifest['missed_by_terraform']}")
    print(f"control refused           {'yes' if manifest['control_refused'] else 'no'}")
    shutil.rmtree(work, ignore_errors=True)


def _first_error(text: str) -> str:
    match = re.search(r"Error: (.+)", text)
    return match.group(1).strip() if match else "error with no summary line"


if __name__ == "__main__":
    main()
