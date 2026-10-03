"""Phase 11.1 local SQLite persistence foundation."""

from database.config import DEFAULT_DATABASE_PATH, get_database_path
from database.connection import connect, transaction
from database.models import EventUpsertResult, MonitoringRunResult
from database.repositories import DatabaseRepository
from database.schema import SCHEMA_VERSION, initialize_database

__all__ = ["DEFAULT_DATABASE_PATH", "get_database_path", "connect", "transaction",
           "EventUpsertResult", "MonitoringRunResult", "DatabaseRepository",
           "SCHEMA_VERSION", "initialize_database"]
