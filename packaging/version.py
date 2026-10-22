#!/usr/bin/env python3
"""Print the launcher version.

The build scripts need the version in shells with different quoting rules, so
it lives here rather than as an inline one-liner in each of them.
"""
from __future__ import annotations

import pathlib
import re
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "openmcl" / "__init__.py"


def version() -> str:
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']', SOURCE.read_text("utf-8"), re.M
    )
    if not match:
        raise SystemExit(f"no __version__ found in {SOURCE}")
    return match.group(1)


if __name__ == "__main__":
    sys.stdout.write(version() + "\n")
