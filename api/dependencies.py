"""Repository construction shared by the API and tests."""

from database import DatabaseRepository


def get_repository(db_path=None):
    return DatabaseRepository(db_path)
