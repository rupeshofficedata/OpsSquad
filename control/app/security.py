from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

ROLE_ORDER = ["viewer", "dev", "admin"]


def role_at_least(user_role: str, required_role: str) -> bool:
    try:
        return ROLE_ORDER.index(user_role) >= ROLE_ORDER.index(required_role)
    except ValueError:
        return False


def require_role(min_role: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify(error="Missing bearer token"), 401
            token = auth.removeprefix("Bearer ")
            try:
                payload = jwt.decode(
                    token,
                    current_app.config["JWT_SECRET"],
                    algorithms=[current_app.config["JWT_ALGORITHM"]],
                )
            except jwt.PyJWTError as exc:
                return jsonify(error=f"Invalid token: {exc}"), 401

            if not role_at_least(payload["role"], min_role):
                return jsonify(error=f"role '{payload['role']}' needs >= '{min_role}'"), 403

            g.user = payload
            return fn(*args, **kwargs)

        return wrapper

    return decorator
