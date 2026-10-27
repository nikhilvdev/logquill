"""Runs every oracle scenario in this interpreter and prints the results as one JSON document.

    python run_all.py [scenario ...]
"""

import json
import sys

from scenarios import run_all

if __name__ == "__main__":
    print(json.dumps(run_all(sys.argv[1:] or None), sort_keys=True, default=str))
