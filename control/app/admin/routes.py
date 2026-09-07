import bcrypt
from flask import Blueprint, g, jsonify, request

from app.db import get_conn
from app.security import require_role

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/users")
@require_role("admin")
def list_users():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, email, full_name, role, is_active, last_login_at, created_at FROM users ORDER BY created_at"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/users")
@require_role("admin")
def create_user():
    body = request.get_json(force=True)
    email = body["email"]
    password = body["password"]
    full_name = body.get("full_name")
    role = body.get("role", "viewer")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

    conn = get_conn()
    row = conn.execute(
        """
        INSERT INTO users (email, password_hash, full_name, role)
        VALUES (%s, %s, %s, %s)
        RETURNING id, email, full_name, role, is_active, created_at
        """,
        (email, password_hash, full_name, role),
    ).fetchone()
    conn.commit()
    return jsonify(dict(row)), 201


@bp.patch("/users/<user_id>/role")
@require_role("admin")
def update_role(user_id: str):
    body = request.get_json(force=True)
    role = body["role"]
    if role not in ("admin", "dev", "viewer"):
        return jsonify(error="invalid role"), 400

    conn = get_conn()
    row = conn.execute(
        "UPDATE users SET role = %s WHERE id = %s RETURNING id, email, role",
        (role, user_id),
    ).fetchone()
    conn.commit()
    if row is None:
        return jsonify(error="user not found"), 404
    return jsonify(dict(row))


@bp.get("/audit")
@require_role("admin")
def audit_log():
    action = request.args.get("action")
    user_id = request.args.get("user_id")
    limit = min(int(request.args.get("limit", 100)), 500)

    query = "SELECT * FROM audit_logs WHERE TRUE"
    params: list = []
    if action:
        query += " AND action = %s"
        params.append(action)
    if user_id:
        query += " AND user_id = %s"
        params.append(user_id)
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    conn = get_conn()
    rows = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])
