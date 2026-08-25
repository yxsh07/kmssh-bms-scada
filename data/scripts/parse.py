"""
data/scripts/parse.py — legacy shim
====================================
This file is kept for backwards-compatibility. The real parser is now at:

    data/parse_export.py

To run the parser:

    python data/parse_export.py [--fmt sqlite|parquet] [--data-dir PATH]

To verify output against manifest.json:

    python data/verify_export.py [--data-dir PATH]
"""

import subprocess
import sys
from pathlib import Path

REAL_PARSER = Path(__file__).parent.parent / "parse_export.py"

if __name__ == "__main__":
    print(
        "[shim] Redirecting to data/parse_export.py  "
        "(this file is a compatibility stub)\n",
        file=sys.stderr,
    )
    # Forward all CLI arguments unchanged to the real parser
    result = subprocess.run(
        [sys.executable, str(REAL_PARSER)] + sys.argv[1:],
        check=False,
    )
    sys.exit(result.returncode)
