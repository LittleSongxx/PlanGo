"""Start only this repository's backend; no external Planora checkout is imported."""

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root / "vendor" / "plango_harness" / "backend")]

if __name__ == "__main__":
    from dotenv import dotenv_values

    for key, value in dotenv_values(root / ".env").items():
        if value is not None:
            os.environ.setdefault(key, value)
    from plango.app import main

    main()
