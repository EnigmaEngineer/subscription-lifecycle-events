"""Nothing outside the standard library may be imported by the package.

This repository has one dependency and it is the test runner. The day that
stops being true, this test fails and requirements.txt gets updated in the same
commit rather than three days later.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = {"subevents"}


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


def test_no_third_party_imports_outside_the_tests():
    stdlib = set(sys.stdlib_module_names)
    offenders = {}
    for path in package_files():
        extra = imported_roots(path) - stdlib - LOCAL
        if extra:
            offenders[os.path.relpath(path, ROOT)] = sorted(extra)
    assert offenders == {}


def test_requirements_declares_the_runner():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
        text = handle.read()
    assert "pytest==" in text
