"""Unit tests for the services layer."""

import pytest

from src.app import services


def test_create_user_rejects_blank() -> None:
    with pytest.raises(ValueError):
        services.create_user("   ")


def test_list_users_defaults_to_first_page() -> None:
    rows = services.list_users()
    assert isinstance(rows, list)
