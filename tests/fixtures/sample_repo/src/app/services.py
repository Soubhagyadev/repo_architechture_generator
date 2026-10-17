"""Business logic for user management. All persistence goes through db.py."""

from src.app import db
from src.app.config import settings


def list_users(page: int = 0) -> list[db.UserRow]:
    """Return one page of users ordered by id."""
    size = settings.max_page_size
    offset = max(page, 0) * size
    return db.fetch_users(limit=size, offset=offset)


def find_user(name: str) -> db.UserRow | None:
    """Look a user up by exact name."""
    return db.fetch_user_by_name(name)


def create_user(name: str) -> db.UserRow:
    """Persist a new user and return the stored row."""
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    return db.insert_user(name.strip())
