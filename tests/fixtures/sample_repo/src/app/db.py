"""Database access layer. The only module allowed to touch the engine."""

from sqlalchemy import create_engine, text

from src.app.config import settings

_engine = create_engine(settings.database_url, future=True)


class UserRow:
    """Plain row object returned by query helpers."""

    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


def fetch_users(limit: int, offset: int) -> list[UserRow]:
    """Fetch a page of users."""
    with _engine.connect() as connection:
        result = connection.execute(
            text("SELECT id, name FROM users ORDER BY id LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        )
        return [UserRow(row.id, row.name) for row in result]


def fetch_user_by_name(name: str) -> UserRow | None:
    """Fetch a single user by name or ``None``."""
    with _engine.connect() as connection:
        result = connection.execute(
            text("SELECT id, name FROM users WHERE name = :name"),
            {"name": name},
        ).first()
        return UserRow(result.id, result.name) if result else None


def insert_user(name: str) -> UserRow:
    """Insert a user and return the persisted row."""
    with _engine.begin() as connection:
        result = connection.execute(
            text("INSERT INTO users (name) VALUES (:name) RETURNING id, name"),
            {"name": name},
        ).first()
        assert result is not None
        return UserRow(result.id, result.name)
