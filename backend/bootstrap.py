"""Container startup: wait for Postgres, seed demo data if needed, then hand off to Streamlit.

  python -m backend.bootstrap

Seeds when the database is empty or its data is from an earlier day (so "today" always
has jobs on a public demo), or when RESEED=1. Approvals made earlier the same day are kept.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import psycopg2

from backend.db import DATABASE_URL


def wait_for_db(url: str, tries: int = 30) -> None:
    for i in range(tries):
        try:
            psycopg2.connect(url).close()
            return
        except psycopg2.OperationalError as e:
            print(f"waiting for Postgres ({i + 1}/{tries}): {str(e).strip()[:80]}", flush=True)
            time.sleep(2)
    sys.exit("Postgres never became reachable")


def data_day(url: str):
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('etl_runs') IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
        cur.execute("SELECT ran_at::date FROM etl_runs ORDER BY run_id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def main() -> None:
    url = DATABASE_URL
    wait_for_db(url)
    today = datetime.now().date()
    last = data_day(url)
    if os.getenv("RESEED") == "1" or last != today:
        from backend.demo_data import load_into
        now = datetime.now().replace(second=0, microsecond=0)
        stats = load_into(url, now)
        print(f"seeded demo data as of {now:%Y-%m-%d %H:%M} — jobs loaded: {stats['jobs.csv']['rows_out']}", flush=True)
    else:
        print(f"data already seeded today ({last}); keeping it", flush=True)


if __name__ == "__main__":
    main()
