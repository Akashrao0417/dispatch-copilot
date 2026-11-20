"""Fake CRM API — stands in for NorthStar's customer system.

Like most real CRMs it is paginated, nests fields, and leaves some contacts blank.

Run:  uvicorn backend.crm_api:app --port 8001
Try:  curl "http://localhost:8001/api/v1/customers?page=1&page_size=25"
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

DATA = Path(__file__).resolve().parents[1] / "data" / "raw" / "crm_customers.json"

app = FastAPI(title="NorthStar CRM (fake)", version="1.0")


def _load() -> list[dict]:
    if not DATA.exists():
        raise HTTPException(503, "CRM data not generated yet — run python data/generate_data.py")
    return json.loads(DATA.read_text())


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/v1/customers")
def list_customers(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
    rows = _load()
    start = (page - 1) * page_size
    chunk = rows[start:start + page_size]
    has_more = start + page_size < len(rows)
    return {"data": chunk, "page": page, "page_size": page_size, "total": len(rows),
            "next_page": page + 1 if has_more else None}


@app.get("/api/v1/customers/{customer_id}")
def get_customer(customer_id: str) -> dict:
    for row in _load():
        if row["id"] == customer_id:
            return row
    raise HTTPException(404, "customer not found")
