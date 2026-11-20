"""Roles and permissions — enforced in the backend, not just hidden in the UI.

Manager     sees every job; may propose and approve all three tools.
Technician  sees only jobs assigned to them; may create tickets on their own jobs.
            They cannot reassign jobs (SOP-RA §4) or approve customer messages (SOP-CU §6).
"""
from __future__ import annotations

from dataclasses import dataclass

TOOLS = ("reassign_technician", "create_ticket", "draft_customer_update")

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
