"""Read queries shared by the dashboard, the copilot and the action agent.

Every job query goes through `job_scope_sql`, so a technician can never read
another technician's jobs, whichever screen or tool asks.

Definitions (same as README):
  Delayed    = not cancelled, not finished by end of the 2h arrival window
  SLA breach = not cancelled, not finished by the tier deadline (Gold 2h / Silver 4h / Bronze 6h)
"""
from __future__ import annotations

from datetime import date, datetime

from backend.auth import User, job_scope_sql

OPEN_STATUSES = ("Scheduled", "En Route", "In Progress")

_JOB_SELECT = """
    WITH last_reason AS (
        SELECT DISTINCT ON (job_id) job_id, ticket_id AS reason_ticket, category AS reason, notes AS reason_note
        FROM tickets
        WHERE category <> 'Routine update'
        ORDER BY job_id, logged_at DESC
    )
    SELECT j.job_id, j.city, j.status, j.priority, j.job_type,
           j.scheduled_at, j.window_end_at, j.sla_due_at, j.completed_at,
           j.customer_id, c.name AS customer, c.tier, c.phone AS customer_phone, c.email AS customer_email,
           j.technician_id, t.name AS technician, t.skill AS technician_skill,
           lr.reason, lr.reason_note, lr.reason_ticket,
           (j.scheduled_at <= %(as_of)s AND j.status <> 'Cancelled'
                AND COALESCE(j.completed_at, %(as_of)s) > j.window_end_at) AS is_delayed,
           (j.scheduled_at <= %(as_of)s AND j.status <> 'Cancelled'
                AND COALESCE(j.completed_at, %(as_of)s) > j.sla_due_at) AS is_breach,
           CASE WHEN j.scheduled_at > %(as_of)s THEN 0 ELSE
                GREATEST(0, EXTRACT(EPOCH FROM COALESCE(j.completed_at, %(as_of)s) - j.window_end_at) / 60)::int
           END AS minutes_late
    FROM jobs j
    JOIN customers c   USING (customer_id)
    JOIN technicians t USING (technician_id)
    LEFT JOIN last_reason lr USING (job_id)
"""


def jobs_for_day(conn, user: User, day: date, as_of: datetime, city: str | None = None) -> list[dict]:
    scope, params = job_scope_sql(user)
    sql = _JOB_SELECT + f" WHERE j.scheduled_at::date = %(day)s AND {scope}"
    params |= {"day": day, "as_of": as_of}
    if city:
        sql += " AND lower(j.city) = lower(%(city)s)"
        params["city"] = city
    sql += " ORDER BY j.scheduled_at, j.job_id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_job(conn, user: User, job_id: str, as_of: datetime) -> dict | None:
    """One job, or None if it doesn't exist OR the user may not see it."""
    scope, params = job_scope_sql(user)
    with conn.cursor() as cur:
        cur.execute(_JOB_SELECT + f" WHERE j.job_id = %(job_id)s AND {scope}",
                    params | {"job_id": job_id.strip().upper(), "as_of": as_of})
        row = cur.fetchone()
    return dict(row) if row else None


def free_technicians(conn, city: str, day: date, exclude: set[str] = frozenset()) -> list[dict]:
    """Technicians based in `city` with no open job on `day` (SOP-RA §3)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.technician_id, t.name, t.skill, t.home_city,
                   (SELECT count(*) FROM jobs j WHERE j.technician_id = t.technician_id
                      AND j.scheduled_at::date = %(day)s AND j.status = 'Completed') AS completed_today
            FROM technicians t
            WHERE lower(t.home_city) = lower(%(city)s)
              AND NOT EXISTS (
                  SELECT 1 FROM jobs j
                  WHERE j.technician_id = t.technician_id
                    AND j.scheduled_at::date = %(day)s
                    AND j.status = ANY(%(open)s))
            ORDER BY completed_today, t.technician_id
        """, {"city": city, "day": day, "open": list(OPEN_STATUSES)})
        rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if r["technician_id"] not in exclude]


def technician(conn, technician_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM technicians WHERE technician_id = %s", (technician_id,))
        row = cur.fetchone()
    return dict(row) if row else None
