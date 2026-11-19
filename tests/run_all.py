"""Run every check. One entry point, used by hand and by the mutation pass.

`python -m pytest` works just as well from the repository root. This exists
because the tooling around the project expects a single runner it can call with
no arguments and no knowledge of which test framework is underneath.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    import pytest

    return pytest.main(["-q", os.path.join(ROOT, "tests")])


if __name__ == "__main__":
    raise SystemExit(main())
