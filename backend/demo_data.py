"""Load a deterministic demo dataset into any Postgres database (used by tests, evals and deploys).

The CRM API is called in-process, so no server needs to be running.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def load_into(database_url: str, as_of: datetime, recreate_db: bool = False) -> dict:
    """Generate the messy sources for `as_of`, clean them, load them. Returns ETL stats."""
    if recreate_db:
        import psycopg2
        base, name = database_url.rsplit("/", 1)
        admin = psycopg2.connect(base + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
        admin.close()

    from backend import etl
    from data.generate_data import build
    raw = build(as_of)
    # Same dtype path as reading jobs.csv from disk
    jobs_raw = pd.DataFrame(raw["jobs"]).astype(str).replace({"nan": "", "None": ""})
    clean, stats = etl.transform(jobs_raw, pd.DataFrame(raw["tickets"]), pd.DataFrame(raw["roster"]), raw["crm"])
    etl.load(clean, stats, database_url)
    return stats
