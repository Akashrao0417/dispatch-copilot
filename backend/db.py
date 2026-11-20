"""Postgres connection helper shared by the ETL, copilot, agent and dashboard."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dispatch")
APP_SCHEMA = Path(__file__).with_name("app_schema.sql")


@contextmanager
def connect(database_url: str | None = None):
    """Yield a connection that commits on success and rolls back on error."""
    conn = psycopg2.connect(database_url or DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_app_tables(conn) -> None:
    """Create app tables (users, actions, audit log, messages) and seed users.

    These tables are NOT dropped by the ETL, so the audit log survives reloads.
    """
    with conn.cursor() as cur:
        cur.execute(APP_SCHEMA.read_text())
        cur.execute("""
            INSERT INTO users (username, name, role, technician_id)
            VALUES ('manager', 'Dana Reyes (Shift Manager)', 'manager', NULL)
            ON CONFLICT (username) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO users (username, name, role, technician_id)
            SELECT lower(technician_id), name, 'technician', technician_id FROM technicians
            ON CONFLICT (username) DO UPDATE SET name = EXCLUDED.name
        """)
