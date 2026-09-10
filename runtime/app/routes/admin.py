from typing import Literal

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import repo
from app.security import User, require_role

router = APIRouter(prefix="/admin", tags=["admin"])

Role = Literal["admin", "dev", "viewer"]


class CreateUserRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    role: Role = "viewer"


class UpdateRoleRequest(BaseModel):
    role: Role


@router.get("/users")
async def list_users(_user: User = Depends(require_role("admin"))):
    return await repo.list_users()


@router.post("/users")
async def create_user(req: CreateUserRequest, _user: User = Depends(require_role("admin"))):
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt(rounds=12)).decode()
    return await repo.create_user(req.email, password_hash, req.full_name, req.role)


@router.patch("/users/{user_id}/role")
async def update_role(user_id: str, req: UpdateRoleRequest, _user: User = Depends(require_role("admin"))):
    updated = await repo.update_user_role(user_id, req.role)
    if updated is None:
        raise HTTPException(404, "user not found")
    return updated


@router.get("/audit")
async def audit_log(
    action: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    _user: User = Depends(require_role("admin")),
):
    return await repo.list_audit_log(action, user_id, min(limit, 500))
