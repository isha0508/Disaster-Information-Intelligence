"""SQLite path configuration for the local persistence foundation."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "database" / "disaster_intelligence.db"


def get_database_path(path=None):
    """Resolve an explicit path, DISASTER_DB_PATH, or the ignored local default."""
    value = path if path is not None else os.environ.get("DISASTER_DB_PATH")
    if value is None or str(value).strip() == "":
        return DEFAULT_DATABASE_PATH
    if str(value) == ":memory:":
        return ":memory:"
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()
