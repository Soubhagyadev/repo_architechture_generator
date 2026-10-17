"""Pydantic request/response schemas for the API layer."""

from pydantic import BaseModel


class UserCreate(BaseModel):
    """Payload for POST /users."""

    name: str


class UserOut(BaseModel):
    """Response shape for user records."""

    id: int
    name: str
