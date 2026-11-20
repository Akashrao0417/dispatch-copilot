"""ETL tests — run with `pytest`. No database needed: the CRM API is called in-process."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend import etl
from backend.crm_api import app as crm_app
from data.generate_data import build


# ---------------------------------------------------------------- cleaners
@pytest.mark.parametrize("raw", ["2026-11-19 09:30", "11/19/2026 09:30 AM", "19-Nov-2026 09:30",
                                 "2026/11/19T09:30:00", "11/19/26 09:30", "2026-11-19T09:30:00"])
def test_parse_date_handles_every_source_format(raw):
    assert etl.parse_date(raw) == datetime(2026, 11, 19, 9, 30)


def test_parse_date_rejects_garbage_and_counts_it():
    st = etl.SourceStats()
    assert etl.parse_date("TBD", st) is None
    assert etl.parse_date("", st) is None
    assert st.fixes["dates_unparseable"] == 1


@pytest.mark.parametrize("raw,expected", [
    ("(704) 555-1234", "+17045551234"), ("+1 704.555.1234", "+17045551234"),
    ("17045551234", "+17045551234"), ("N/A", None), ("", None), ("12345", None)])
def test_normalize_phone(raw, expected):
    assert etl.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("CLT", "Charlotte"), (" charlotte ", "Charlotte"), ("Charlotte, NC", "Charlotte"),
    ("Winston Salem", "Winston-Salem"), ("GSO", "Greensboro"), ("RALEIGH", "Raleigh")])
def test_normalize_city(raw, expected):
    assert etl.normalize_city(raw) == expected


@pytest.mark.parametrize("raw", ["T-014", "T014", "t-14", " t-014 "])
def test_normalize_tech_id(raw):
    assert etl.normalize_tech_id(raw) == "T-014"


# ---------------------------------------------------------------- full transform
@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import pandas as pd
    raw = build(datetime(2026, 11, 19, 14, 0))
    d = tmp_path_factory.mktemp("raw")
    pd.DataFrame(raw["jobs"]).to_csv(d / "jobs.csv", index=False)
    jobs = etl.extract_jobs(d / "jobs.csv")
    tickets = pd.DataFrame(raw["tickets"])
    roster = pd.DataFrame(raw["roster"])
    return etl.transform(jobs, tickets, roster, raw["crm"])


def test_row_counts_and_drops(run):
    clean, stats = run
    j = stats["jobs.csv"]
    assert j["rows_in"] == 205
    assert j["duplicates_removed"] == 5
    assert j["dropped_reasons"] == {"missing customer id": 1, "customer not in CRM": 1,
                                    "unknown technician": 1, "unparseable scheduled date": 1}
    assert j["rows_out"] == len(clean["jobs"]) == 196
    assert len(clean["technicians"]) == 120
    assert stats["service_logs.xlsx (Service Logs)"]["duplicates_removed"] == 18


def test_clean_tables_are_consistent(run):
    clean, _ = run
    jobs, tickets = clean["jobs"], clean["tickets"]
    assert jobs["job_id"].is_unique and tickets["ticket_id"].is_unique
    assert jobs["customer_id"].isin(clean["customers"]["customer_id"]).all()
    assert jobs["technician_id"].isin(clean["technicians"]["technician_id"]).all()
    assert tickets["job_id"].isin(jobs["job_id"]).all()
    assert set(jobs["city"]) <= {"Charlotte", "Raleigh", "Durham", "Greensboro", "Winston-Salem"}
    assert jobs["scheduled_at"].notna().all()
    assert (jobs["sla_due_at"] >= jobs["window_end_at"]).all()  # Gold = 2h = window


def test_crm_api_pagination(tmp_path, monkeypatch):
    import json
    from backend import crm_api
    rows = build(datetime(2026, 11, 19, 14, 0))["crm"]
    f = tmp_path / "crm.json"
    f.write_text(json.dumps(rows))
    monkeypatch.setattr(crm_api, "DATA", f)
    with TestClient(crm_app) as client:
        got = etl.extract_crm(client)
    assert len(got) == 80 and got[0]["id"] == "C-001"
