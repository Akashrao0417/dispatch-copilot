"""Passwords, the permission matrix, the question router, and container startup."""
from datetime import datetime

import pytest

from backend import auth, copilot
from backend.auth import PermissionDenied, User, check_tool, hash_password, verify_password

MGR = User("manager", "Dana", "manager")
TECH = User("t-001", "Priya", "technician", "T-001")
OWN_JOB = {"job_id": "J-1", "technician_id": "T-001"}
OTHER_JOB = {"job_id": "J-2", "technician_id": "T-002"}


# ---------------------------------------------------------------- passwords (no DB)
def test_password_hash_roundtrip_and_salting():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b                                   # random salt
    assert verify_password("correct horse", a) and verify_password("correct horse", b)
    assert not verify_password("wrong horse", a)
    assert "correct horse" not in a                 # never stored in clear


def test_password_rules_and_tampering():
    with pytest.raises(ValueError):
        hash_password("short")
    h = hash_password("correct horse")
    assert not verify_password("correct horse", h[:-2] + "00")      # altered hash
    assert not verify_password("correct horse", "md5$1$aa$bb")      # unknown algorithm
    assert not verify_password("correct horse", None)
    assert not verify_password("correct horse", "garbage")


# ---------------------------------------------------------------- permission matrix (no DB)
@pytest.mark.parametrize("user,tool,job,allowed", [
    (MGR, "reassign_technician", OTHER_JOB, True),
    (MGR, "create_ticket", OTHER_JOB, True),
    (MGR, "draft_customer_update", OTHER_JOB, True),
    (TECH, "create_ticket", OWN_JOB, True),
    (TECH, "create_ticket", OTHER_JOB, False),          # not their job
    (TECH, "reassign_technician", OWN_JOB, False),      # SOP-RA §4
    (TECH, "draft_customer_update", OWN_JOB, False),    # SOP-CU §6
    (MGR, "delete_job", OWN_JOB, False),                # only 3 tools exist
])
def test_permission_matrix(user, tool, job, allowed):
    if allowed:
        check_tool(user, tool, job)
    else:
        with pytest.raises(PermissionDenied):
            check_tool(user, tool, job)


def test_technician_scope_sql_is_parameterized():
    sql, params = auth.job_scope_sql(TECH)
    assert "%(scope_tech)s" in sql and params == {"scope_tech": "T-001"}   # no string-built SQL
    assert auth.job_scope_sql(MGR) == ("TRUE", {})


# ---------------------------------------------------------------- router (no DB)
@pytest.mark.parametrize("q,is_lookup", [
    ("Which jobs are late in Charlotte today, and why?", True),
    ("Any SLA breaches in Raleigh today?", True),
    ("Which of my jobs are late today?", True),
    ("show me delayed jobs in greensboro", True),
    ("Which delayed jobs should I handle first?", False),          # procedure -> SOP
    ("How often should dispatchers check the delayed jobs list?", False),
    ("When does a job count as delayed?", False),
    ("What are the SLA deadlines for Gold customers?", False),
    ("What credit does a Gold customer get for a late job?", False),
])
def test_router_separates_lookups_from_procedures(q, is_lookup):
    assert copilot.is_job_question(q) is is_lookup


# ---------------------------------------------------------------- DB-backed: login + startup
TEST_DB = "postgresql://postgres:postgres@localhost:5432/dispatch_authtest"


@pytest.fixture(scope="module")
def loaded_db():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres").close()
    except psycopg2.OperationalError:
        pytest.skip("Postgres not reachable")
    from backend.demo_data import load_into
    load_into(TEST_DB, datetime(2026, 11, 19, 14, 0), recreate_db=True)
    return TEST_DB


def test_authenticate_against_db(loaded_db):
    from backend import db
    with db.connect(loaded_db) as conn:
        auth.set_password(conn, "t-001", "tech-pass-123")
    with db.connect(loaded_db) as conn:
        assert auth.authenticate(conn, "T-001 ", "tech-pass-123") == User("t-001", auth.get_user(conn, "t-001").name,
                                                                         "technician", "T-001")
        assert auth.authenticate(conn, "t-001", "wrong-pass-123") is None
        assert auth.authenticate(conn, "manager", "anything-123") is None      # no password set -> can't log in
        assert auth.authenticate(conn, "nobody", "tech-pass-123") is None
        with pytest.raises(PermissionDenied):
            auth.set_password(conn, "nobody", "whatever-123")


def test_bootstrap_keeps_todays_data_and_reseeds_stale(loaded_db, monkeypatch):
    import psycopg2
    from backend import bootstrap
    monkeypatch.setattr(bootstrap, "DATABASE_URL", loaded_db)
    with psycopg2.connect(loaded_db) as c, c.cursor() as cur:
        cur.execute("UPDATE etl_runs SET ran_at = now()::date - 3")        # pretend the data is old
    monkeypatch.delenv("RESEED", raising=False)
    bootstrap.main()                                                     # stale -> reseeds
    assert bootstrap.data_day(loaded_db) == datetime.now().date()
    with psycopg2.connect(loaded_db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM etl_runs")
        runs = cur.fetchone()[0]
    bootstrap.main()                                                     # fresh -> keeps
    with psycopg2.connect(loaded_db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM etl_runs")
        assert cur.fetchone()[0] == runs
