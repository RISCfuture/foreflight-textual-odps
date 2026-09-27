#!/usr/bin/env python3
"""Entry point for the Textual ODPs ForeFlight content pack builder.

Implementation lives in the ``odp_kml`` package; this script is a thin shim
so the GitHub Actions workflow and README can call a single file by name.
Equivalent: ``python -m odp_kml [flags]``.
"""

import sys

from odp_kml.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
