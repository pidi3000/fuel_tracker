"""The app version, shown in the web UI.

Docker images get it from the `APP_VERSION` build argument (e.g. `1.2.3` for releases,
`1.2.3-test+a1b2c3d` for test images). Without it, the `VERSION` file in the repository
root is used, marked as a development build.
"""

import os
from functools import lru_cache
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[3] / "VERSION"


@lru_cache
def get_version() -> str:
    if version := os.environ.get("APP_VERSION", "").strip():
        return version
    try:
        return f"{VERSION_FILE.read_text().strip()}-dev"
    except OSError:
        return "unknown"
