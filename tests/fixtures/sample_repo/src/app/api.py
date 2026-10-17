"""REST API layer. Routes delegate all business logic to the services layer."""

from fastapi import APIRouter, HTTPException

from src.app import services
from src.app.schemas import UserCreate, UserOut

router = APIRouter(prefix="/api")


@router.get("/users", response_model=list[UserOut])
def list_users(page: int = 0) -> list[UserOut]:
    """List users page by page."""
    rows = services.list_users(page=page)
    return [UserOut(id=row.id, name=row.name) for row in rows]


@router.post("/users", response_model=UserOut)
def create_user(payload: UserCreate) -> UserOut:
    """Create a user; 409 when the name is already taken."""
    existing = services.find_user(payload.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail="user exists")
    row = services.create_user(payload.name)
    return UserOut(id=row.id, name=row.name)
