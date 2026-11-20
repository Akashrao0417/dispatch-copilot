"""Phase 2-3 tests: SOP RAG, roles, action agent, audit log.

RAG tests need no database. The rest load fresh data into a throwaway Postgres database
(`dispatch_test`) and are skipped when Postgres isn't reachable.
"""
import os
from datetime import datetime

import pytest

from backend import rag

AS_OF = datetime(2026, 11, 19, 14, 0)


# ================================================================ RAG (no DB)
@pytest.mark.parametrize("question,cite", [
    ("When is a job delayed?", "SOP-LD §2"),
    ("What credit does a Gold customer get for an SLA breach?", "SOP-RF §3"),
    ("Who can reassign a job?", "SOP-RA §4"),
    ("How many times can a job be reassigned?", "SOP-RA §7"),
    ("What should a technician do in a lightning storm?", "SOP-SC §4"),
    ("van broke down on the highway what do I do", "SOP-SC §5"),
    ("What goes in a customer update message?", "SOP-CU §3"),
])
def test_rag_cites_the_right_section(question, cite):
    a = rag.ask_sop(question, client=False)
    assert not a.is_idk
    assert a.citations[0].label == cite
    assert f"[{cite}]" in a.text
    assert a.citations[0].file.endswith(".pdf") and a.citations[0].page == 1


@pytest.mark.parametrize("question", [
    "What is the company vacation policy?", "Who won the Super Bowl?",
    "what is the wifi password at the depot", "What is the dress code for technicians?", "",
])
def test_rag_says_i_dont_know(question):
    a = rag.ask_sop(question, client=False)
    assert a.text == "I don't know" and a.citations == []


def test_all_five_sops_load_from_pdf():
    chunks = rag.load_chunks()
    assert {c.sop_id for c in chunks} == {"SOP-LD", "SOP-RF", "SOP-RA", "SOP-SC", "SOP-CU"}
    assert all(c.text for c in chunks)


class FakeClient:
    """Stands in for Claude so the LLM path is tested without an API key."""
    def __init__(self, reply):
        self.reply = reply
        self.messages = self

    def create(self, **kw):
        self.last = kw
        block = type("B", (), {"type": "text", "text": self.reply})
        return type("M", (), {"content": [block]})


def test_llm_answer_keeps_valid_citations():
    fake = FakeClient("Gold customers get 25% of the job fee for an SLA breach [SOP-RF §3].")
    a = rag.ask_sop("What credit does a Gold customer get for an SLA breach?", client=fake)
    assert a.mode == "claude" and [c.label for c in a.citations] == ["SOP-RF §3"]
    assert "SOP-RF §3" in fake.last["messages"][0]["content"]  # the section was in the prompt


def test_llm_answer_without_citation_becomes_idk():
    a = rag.ask_sop("What credit does a Gold customer get?", client=FakeClient("Probably around 30%."))
    assert a.is_idk


def test_llm_invented_citation_is_rejected():
    a = rag.ask_sop("What credit does a Gold customer get for an SLA breach?",
                    client=FakeClient("You get 50% [SOP-ZZ §9]."))
    assert a.is_idk


def test_llm_idk_passes_through():
    assert rag.ask_sop("What credit does a Gold customer get?", client=FakeClient("I don't know")).is_idk


# ================================================================ DB-backed
TEST_DB = os.getenv("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dispatch_test")


@pytest.fixture(scope="module")
def dbconn():
    psycopg2 = pytest.importorskip("psycopg2")
    admin_url = TEST_DB.rsplit("/", 1)[0] + "/postgres"
    try:
        admin = psycopg2.connect(admin_url)
    except psycopg2.OperationalError:
        pytest.skip("Postgres not reachable")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS dispatch_test")
        cur.execute("CREATE DATABASE dispatch_test")
    admin.close()

    import pandas as pd
    from backend import db, etl
    from data.generate_data import build
    raw = build(AS_OF)
    jobs_raw = pd.DataFrame(raw["jobs"]).astype(str).replace({"nan": ""})
    clean, stats = etl.transform(jobs_raw, pd.DataFrame(raw["tickets"]), pd.DataFrame(raw["roster"]), raw["crm"])
    etl.load(clean, stats, TEST_DB)
    with db.connect(TEST_DB) as conn:
        db.init_app_tables(conn)
    with db.connect(TEST_DB) as conn:
        yield conn


@pytest.fixture
def users(dbconn):
    from backend.auth import get_user
    from backend import queries
    mgr = get_user(dbconn, "manager")
    delayed = [j for j in queries.jobs_for_day(dbconn, mgr, AS_OF.date(), AS_OF)
               if j["is_delayed"] and j["status"] in queries.OPEN_STATUSES]
    owner = get_user(dbconn, delayed[0]["technician_id"].lower())
    other = next(get_user(dbconn, f"t-{i:03d}") for i in range(1, 121)
                 if f"T-{i:03d}" != owner.technician_id)
    return {"manager": mgr, "owner": owner, "other": other, "job": delayed[0]}


def test_manager_sees_all_technician_sees_own(dbconn, users):
    from backend import queries
    all_jobs = queries.jobs_for_day(dbconn, users["manager"], AS_OF.date(), AS_OF)
    mine = queries.jobs_for_day(dbconn, users["owner"], AS_OF.date(), AS_OF)
    assert len(mine) < len(all_jobs)
    assert mine and all(j["technician_id"] == users["owner"].technician_id for j in mine)


def test_technician_cannot_open_someone_elses_job(dbconn, users):
    from backend import queries
    assert queries.get_job(dbconn, users["other"], users["job"]["job_id"], AS_OF) is None
    assert queries.get_job(dbconn, users["owner"], users["job"]["job_id"], AS_OF) is not None


def test_copilot_job_answer_is_scoped_and_proven(dbconn, users):
    from backend import copilot
    city = users["job"]["city"]
    m = copilot.ask(dbconn, users["manager"], f"Which jobs are late in {city} today, and why?", AS_OF)
    t = copilot.ask(dbconn, users["other"], f"Which jobs are late in {city} today, and why?", AS_OF)
    assert m.kind == "jobs" and users["job"]["job_id"] in m.text
    assert users["job"]["job_id"] not in t.text
    assert all(r["city"] == city for r in m.rows)
    assert [c.label for c in m.citations] == ["SOP-LD §5"]


def test_copilot_unknown_question_is_idk(dbconn, users):
    from backend import copilot
    assert copilot.ask(dbconn, users["manager"], "What's the parental leave policy?", AS_OF).text == "I don't know"


def _count(conn, sql, *args):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchone().values())[0]


def test_nothing_runs_until_approved_then_logged(dbconn, users):
    from backend import agent
    mgr, job = users["manager"], users["job"]
    before = _count(dbconn, "SELECT count(*) FROM tickets WHERE job_id = %s", job["job_id"])
    aid = agent.propose(dbconn, mgr, "create_ticket",
                        {"job_id": job["job_id"], "category": "Customer contact", "notes": "Called customer"},
                        proposed_by="copilot", as_of=AS_OF)
    assert _count(dbconn, "SELECT count(*) FROM tickets WHERE job_id = %s", job["job_id"]) == before
    res = agent.approve(dbconn, mgr, aid, as_of=AS_OF)
    assert _count(dbconn, "SELECT count(*) FROM tickets WHERE job_id = %s", job["job_id"]) == before + 1
    events = [r["event"] for r in agent.audit_entries(dbconn, mgr) if r["action_id"] == aid]
    assert events == ["executed", "approved", "proposed"]           # newest first
    log = [r for r in agent.audit_entries(dbconn, mgr) if r["action_id"] == aid and r["event"] == "approved"][0]
    assert log["actor"] == "manager" and log["ts"] == AS_OF        # who, what, when
    assert res["ticket_id"].startswith("TK-")
    with pytest.raises(agent.ActionError):                          # can't run twice
        agent.approve(dbconn, mgr, aid, as_of=AS_OF)


def test_reassign_executes_and_respects_rules(dbconn, users):
    from backend import agent, queries
    mgr, job = users["manager"], users["job"]
    free = queries.free_technicians(dbconn, job["city"], AS_OF.date(), exclude={job["technician_id"]})
    assert free, "test data should have a free technician"
    aid = agent.propose(dbconn, mgr, "reassign_technician",
                        {"job_id": job["job_id"], "technician_id": free[0]["technician_id"], "reason": "breakdown"},
                        as_of=AS_OF)
    res = agent.approve(dbconn, mgr, aid, as_of=AS_OF)
    assert res["to"] == free[0]["technician_id"]
    assert queries.get_job(dbconn, mgr, job["job_id"], AS_OF)["technician_id"] == free[0]["technician_id"]
    # wrong city is refused
    other_city = _count(dbconn, "SELECT technician_id FROM technicians WHERE home_city <> %s LIMIT 1", job["city"])
    with pytest.raises(agent.ActionError, match="SOP-RA §3"):
        agent.propose(dbconn, mgr, "reassign_technician",
                      {"job_id": job["job_id"], "technician_id": other_city, "reason": "x"}, as_of=AS_OF)


def test_technician_permissions(dbconn, users):
    from backend import agent
    from backend.auth import PermissionDenied
    owner, other, job = users["owner"], users["other"], users["job"]
    # own job: may create a ticket (still needs their Approve click)
    aid = agent.propose(dbconn, owner, "create_ticket",
                        {"job_id": job["job_id"], "category": "Traffic delay", "notes": "I-77 closed"}, as_of=AS_OF)
    agent.approve(dbconn, owner, aid, as_of=AS_OF)
    # may not reassign or message customers
    with pytest.raises(PermissionDenied):
        agent.propose(dbconn, owner, "reassign_technician",
                      {"job_id": job["job_id"], "technician_id": "T-001", "reason": "x"}, as_of=AS_OF)
    with pytest.raises(PermissionDenied):
        agent.propose(dbconn, owner, "draft_customer_update",
                      {"job_id": job["job_id"], "channel": "email",
                       "message": f"Hello about job {job['job_id']}, running late."}, as_of=AS_OF)
    # another technician can't touch this job at all
    with pytest.raises(PermissionDenied):
        agent.propose(dbconn, other, "create_ticket",
                      {"job_id": job["job_id"], "category": "Traffic delay", "notes": "x"}, as_of=AS_OF)
    # and a manager-proposed customer message can't be approved by a technician
    mid = agent.propose(dbconn, users["manager"], "draft_customer_update",
                        {"job_id": job["job_id"], "channel": "email",
                         "message": f"Hi, about job {job['job_id']}: we are running late, sorry."}, as_of=AS_OF)
    with pytest.raises(PermissionDenied):
        agent.approve(dbconn, owner, mid, as_of=AS_OF)
    denied = [r for r in agent.audit_entries(dbconn, users["manager"]) if r["event"] == "denied"]
    assert len(denied) >= 4                                          # refusals are logged too


def test_only_three_tools(dbconn, users):
    from backend import agent
    assert [t["name"] for t in agent.TOOL_SPECS] == ["reassign_technician", "create_ticket", "draft_customer_update"]
    with pytest.raises(agent.ActionError):
        agent.propose(dbconn, users["manager"], "delete_job", {"job_id": users["job"]["job_id"]}, as_of=AS_OF)


def test_rule_planner_follows_sops(dbconn, users):
    from backend import agent
    res = agent.suggest(dbconn, users["manager"], AS_OF, limit=5, client=False)
    assert res["planner"] == "rules" and res["created"]
    pending = [a for a in agent.list_actions(dbconn, users["manager"], "pending") if a["proposed_by"] == "copilot"]
    msgs = [a for a in pending if a["tool"] == "draft_customer_update"]
    assert msgs and all(a["job_id"] in a["args"]["message"] for a in msgs)          # SOP-CU §3
    assert all(a["citations"] for a in pending)
    # running it again doesn't duplicate pending actions
    again = agent.suggest(dbconn, users["manager"], AS_OF, limit=5, client=False)
    assert not [p for p, _ in again["skipped"]] and len(again["created"]) == 0
