import jwt
from fastapi import Depends, HTTPException, Header
from pydantic import BaseModel

from app.config import settings

# Role order for RBAC comparisons — index = privilege level.
ROLE_ORDER = ["viewer", "dev", "admin"]


class User(BaseModel):
    id: str
    email: str
    role: str


def role_at_least(user_role: str, required_role: str) -> bool:
    """True if user_role has >= privilege of required_role."""
    try:
        return ROLE_ORDER.index(user_role) >= ROLE_ORDER.index(required_role)
    except ValueError:
        return False


async def current_user(authorization: str | None = Header(default=None)) -> User:
    """Decode and verify the JWT the BFF forwards. FastAPI never trusts the
    BFF's own RBAC check — every mutating route re-verifies here."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid token: {exc}") from exc
    return User(id=payload["sub"], email=payload["email"], role=payload["role"])


def require_role(min_role: str):
    async def _check(user: User = Depends(current_user)) -> User:
        if not role_at_least(user.role, min_role):
            raise HTTPException(403, f"role '{user.role}' cannot access this resource, needs >= '{min_role}'")
        return user

    return _check
