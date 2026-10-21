#!/usr/bin/env python3
"""Start OpenMCL."""
import sys

if sys.version_info < (3, 10):
    raise SystemExit("OpenMCL needs Python 3.10 or newer.")

from openmcl.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
