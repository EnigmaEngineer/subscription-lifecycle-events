"""An import outside the standard library has to be declared somewhere.

This repository had one dependency and it was the test runner. The warehouse
scripts added three more on 2026-11-21 and they are in their own requirements
file, because the suite does not need them and a clone with neither dbt nor
duckdb still grades the figures they produce.

So the rule is no longer that nothing third party is imported. It is that
every third party import is declared, and the distribution to module mapping
is here rather than in the gate, because a distribution name and a module name
are different strings and comparing them directly is how two declared packages
get reported as undeclared.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# A distribution installs a module whose name it does not have to share.
DIST_TO_MODULE = {
    "dbt-core": "dbt",
    "dbt-duckdb": "dbt",
    "dbt-snowflake": "dbt",
    "pytest": "pytest",
    "duckdb": "duckdb",
}


def declared_modules():
    """Every module name any requirements file in this repository declares."""
    out = set()
    for entry in sorted(os.listdir(ROOT)):
        if not (entry.startswith("requirements") and entry.endswith(".txt")):
            continue
        with open(os.path.join(ROOT, entry), encoding="utf-8") as handle:
            for line in handle:
                line = line.split("#")[0].strip()
                if not line:
                    continue
                dist = re.split(r"[=<>!~\[]", line)[0].strip()
                out.add(DIST_TO_MODULE.get(dist, dist))
    return out


def local_names():
    """Everything in this repository that a module here can legitimately import.

    The package, plus every script, because `scripts/measure.py` imports
    `scripts/replay.py` to avoid computing the same figures twice. Listing them
    by rule rather than by name keeps a real third party import failing.
    """
    names = {"subevents"}
    for entry in os.listdir(os.path.join(ROOT, "scripts")):
        if entry.endswith(".py"):
            names.add(entry[:-3])
    return names


def imported_roots(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def package_files():
    out = []
    for folder in ("subevents", "scripts"):
        base = os.path.join(ROOT, folder)
        for name in sorted(os.listdir(base)):
            if name.endswith(".py"):
                out.append(os.path.join(base, name))
    return out


def test_the_scan_sees_files():
    assert len(package_files()) >= 6


def test_every_third_party_import_is_declared():
    stdlib = set(sys.stdlib_module_names)
    allowed = stdlib | local_names() | declared_modules()
    offenders = {}
    for path in package_files():
        extra = imported_roots(path) - allowed
        if extra:
            offenders[os.path.relpath(path, ROOT)] = sorted(extra)
    assert offenders == {}


def test_the_package_itself_stays_on_the_standard_library():
    """The warehouse dependencies are for the scripts and not for subevents.

    A module under subevents/ importing duckdb would mean the suite could not
    run on a machine without it, and the whole reason the figures go through a
    manifest is that it can.
    """
    stdlib = set(sys.stdlib_module_names)
    allowed = stdlib | local_names()
    offenders = {}
    for path in package_files():
        if os.path.sep + "subevents" + os.path.sep not in path:
            continue
        extra = imported_roots(path) - allowed
        if extra:
            offenders[os.path.relpath(path, ROOT)] = sorted(extra)
    assert offenders == {}


def test_the_declared_set_is_not_empty():
    """A declared set that reads nothing turns the rule above into no rule."""
    assert "pytest" in declared_modules()
    assert "duckdb" in declared_modules()


def test_requirements_declares_the_runner():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
        text = handle.read()
    assert "pytest==" in text


def test_the_warehouse_requirements_pin_every_version():
    """An unpinned dbt is a different dbt next month and these figures are dated."""
    with open(os.path.join(ROOT, "requirements-warehouse.txt"), encoding="utf-8") as handle:
        lines = [
            line.split("#")[0].strip()
            for line in handle
            if line.split("#")[0].strip()
        ]
    assert lines
    for line in lines:
        assert "==" in line, line
