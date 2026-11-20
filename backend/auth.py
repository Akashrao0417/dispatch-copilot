"""Roles and permissions — enforced in the backend, not just hidden in the UI.

Manager     sees every job; may propose and approve all three tools.
Technician  sees only jobs assigned to them; may create tickets on their own jobs.
            They cannot reassign jobs (SOP-RA §4) or approve customer messages (SOP-CU §6).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

TOOLS = ("reassign_technician", "create_ticket", "draft_customer_update")

# "demo": one-click role picker (public demo, fictional data only).
# "password": real sign-in; users need a password set with `python -m backend.set_password <username>`.
AUTH_MODE = os.getenv("AUTH_MODE", "demo").strip().lower()
PBKDF2_ITERATIONS = 310_000

PERMISSIONS = {
    "manager": set(TOOLS),
    "technician": {"create_ticket"},
}


@dataclass(frozen=True)
class User:
    username: str
    name: str
    role: str
    technician_id: str | None = None

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"


class PermissionDenied(Exception):
    pass


def get_user(conn, username: str) -> User:
    with conn.cursor() as cur:
        cur.execute("SELECT username, name, role, technician_id FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
    if not row:
        raise PermissionDenied(f"unknown user {username!r}")
    return User(**row)


def list_users(conn) -> list[User]:
    with conn.cursor() as cur:
        cur.execute("SELECT username, name, role, technician_id FROM users "
                    "ORDER BY role, technician_id NULLS FIRST")
        return [User(**r) for r in cur.fetchall()]


def job_scope_sql(user: User, alias: str = "j") -> tuple[str, dict]:
    """SQL predicate restricting jobs to what this user may see."""
    if user.is_manager:
        return "TRUE", {}
    return f"{alias}.technician_id = %(scope_tech)s", {"scope_tech": user.technician_id}


def can_see_job(user: User, job: dict) -> bool:
    return user.is_manager or job.get("technician_id") == user.technician_id


def check_tool(user: User, tool: str, job: dict) -> None:
    """Raise PermissionDenied unless `user` may run `tool` on `job`."""
    if tool not in TOOLS:
        raise PermissionDenied(f"unknown tool {tool!r}")
    if not can_see_job(user, job):
        raise PermissionDenied(f"{user.username} cannot access job {job.get('job_id')}")
    if tool not in PERMISSIONS[user.role]:
        raise PermissionDenied(f"role {user.role!r} may not use {tool}")


# --------------------------------------------------------------------------- passwords
def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    """PBKDF2-SHA256 with a random salt, stored as 'pbkdf2_sha256$iterations$salt_hex$hash_hex'."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), hash_hex)  # constant-time


def set_password(conn, username: str, password: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash = %s WHERE username = %s", (hash_password(password), username))
        if cur.rowcount != 1:
            raise PermissionDenied(f"unknown user {username!r}")


def authenticate(conn, username: str, password: str) -> User | None:
    """Return the user if the password matches, else None (same answer for unknown users)."""
    with conn.cursor() as cur:
        cur.execute("SELECT username, name, role, technician_id, password_hash FROM users WHERE username = %s",
                    (username.strip().lower(),))
        row = cur.fetchone()
    stored = row["password_hash"] if row else None
    if not verify_password(password, stored or hash_password("x" * 12)):  # same work for unknown users
        return None
    if not row or not stored:
        return None
    return User(row["username"], row["name"], row["role"], row["technician_id"])
