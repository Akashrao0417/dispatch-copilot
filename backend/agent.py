"""Action agent: proposes actions, a person approves, then it runs — and everything is logged.

Exactly three tools:
  reassign_technician(job_id, technician_id, reason)       — managers only (SOP-RA §4)
  create_ticket(job_id, category, notes)                   — managers, or a technician on their own job
  draft_customer_update(job_id, message, channel)          — managers only (SOP-CU §6)

Lifecycle of every action (stored in proposed_actions, logged in audit_log):

  propose() ──► pending ──approve()──► executed   (or failed, with the error)
                        └─reject()───► rejected

Nothing touches jobs/tickets/messages until a person calls approve(). Permissions and
the tool's own checks are re-run at approval time, because the world may have changed
since the proposal (e.g. the "free" technician got another job).

Planning:
  - with ANTHROPIC_API_KEY, Claude sees the delayed jobs + free technicians + SOP rules and
    proposes tool calls (never executes them);
  - without a key, a rule-based planner applies SOP-LD §5, SOP-RA §3 and SOP-CU §5 directly.
  Both go through the same validation before anything is stored.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from psycopg2.extras import Json

from backend import llm, queries
from backend.auth import TOOLS, PermissionDenied, User, check_tool

MAX_REASSIGNMENTS = 2                        # SOP-RA §7
TICKET_CATEGORIES = ["Traffic delay", "Vehicle breakdown", "Parts missing", "Customer not home",
                     "Address issue", "Weather", "Customer contact", "Refund request",
                     "Reassignment request", "Safety", "Routine update"]
CHANNELS = ["sms", "email"]

TOOL_SPECS = [
    {"name": "reassign_technician",
     "description": "Move a job to another technician. Only to a technician based in the job's city with no "
                    "open jobs today (SOP-RA §3).",
     "input_schema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "technician_id": {"type": "string"},
         "reason": {"type": "string", "description": "One sentence, citing the SOP section."}},
         "required": ["job_id", "technician_id", "reason"]}},
    {"name": "create_ticket",
     "description": "Log a service ticket on a job.",
     "input_schema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "category": {"type": "string", "enum": TICKET_CATEGORIES},
         "notes": {"type": "string"}}, "required": ["job_id", "category", "notes"]}},
    {"name": "draft_customer_update",
     "description": "Draft a customer message following the SOP-CU §5 template. It is only a draft until approved.",
     "input_schema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "message": {"type": "string"},
         "channel": {"type": "string", "enum": CHANNELS}}, "required": ["job_id", "message", "channel"]}},
]


class ActionError(Exception):
    pass


# --------------------------------------------------------------------------- audit
def audit(conn, user_or_actor, event: str, *, action_id=None, tool=None, job_id=None, details=None, ts=None) -> None:
    actor = user_or_actor.username if isinstance(user_or_actor, User) else str(user_or_actor)
    role = user_or_actor.role if isinstance(user_or_actor, User) else "system"
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO audit_log (ts, actor, role, event, action_id, tool, job_id, details)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (ts or datetime.now(), actor, role, event, action_id, tool, job_id, Json(details or {})))


def reassign_count(conn, job_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM audit_log WHERE event = 'executed' "
                    "AND tool = 'reassign_technician' AND job_id = %s", (job_id,))
        return cur.fetchone()["n"]


# --------------------------------------------------------------------------- validation
def validate(conn, user: User, tool: str, args: dict, as_of: datetime) -> dict:
    """Check permissions + tool rules. Returns the job. Raises PermissionDenied / ActionError."""
    if tool not in TOOLS:
        raise ActionError(f"unknown tool {tool!r} — allowed: {', '.join(TOOLS)}")
    job_id = str(args.get("job_id", "")).strip().upper()
    job = queries.get_job(conn, user, job_id, as_of)
    if job is None:
        # Same message whether the job doesn't exist or belongs to someone else: don't leak.
        raise PermissionDenied(f"job {job_id or '?'} not found")
    check_tool(user, tool, job)

    if tool == "reassign_technician":
        new = str(args.get("technician_id", "")).strip().upper()
        if not args.get("reason"):
            raise ActionError("a reason is required")
        if job["status"] in ("Completed", "Cancelled"):
            raise ActionError(f"{job_id} is {job['status']} — nothing to reassign")
        if new == job["technician_id"]:
            raise ActionError(f"{job_id} is already assigned to {new}")
        tech = queries.technician(conn, new)
        if not tech:
            raise ActionError(f"unknown technician {new}")
        if tech["home_city"].lower() != job["city"].lower():
            raise ActionError(f"{new} is based in {tech['home_city']}, job is in {job['city']} (SOP-RA §3)")
        free = {t["technician_id"] for t in queries.free_technicians(conn, job["city"], job["scheduled_at"].date())}
        if new not in free:
            raise ActionError(f"{new} has open jobs today, not free (SOP-RA §3)")
        if reassign_count(conn, job_id) >= MAX_REASSIGNMENTS:
            raise ActionError(f"{job_id} was already reassigned {MAX_REASSIGNMENTS} times — needs the shift manager (SOP-RA §7)")
    elif tool == "create_ticket":
        if args.get("category") not in TICKET_CATEGORIES:
            raise ActionError(f"category must be one of: {', '.join(TICKET_CATEGORIES)}")
        if not str(args.get("notes", "")).strip():
            raise ActionError("notes are required")
    elif tool == "draft_customer_update":
        msg = str(args.get("message", "")).strip()
        if len(msg) < 20:
            raise ActionError("message is too short")
        if job_id not in msg:
            raise ActionError("message must include the job ID (SOP-CU §3)")
        if args.get("channel") not in CHANNELS:
            raise ActionError("channel must be sms or email")
        if args["channel"] == "sms" and not job["customer_phone"]:
            raise ActionError("no phone on file — use email (SOP-CU §4)")
    return job


# --------------------------------------------------------------------------- propose / approve / reject
def propose(conn, user: User, tool: str, args: dict, *, rationale: str = "", citations: list[str] | None = None,
            proposed_by: str | None = None, as_of: datetime | None = None) -> int:
    """Store a pending action after validating it as `user` (the person who will see/approve it)."""
    as_of = as_of or datetime.now()
    args = {**args, "job_id": str(args.get("job_id", "")).strip().upper()}
    actor = proposed_by or user.username
    try:
        validate(conn, user, tool, args, as_of)
    except (PermissionDenied, ActionError) as e:
        audit(conn, user if actor == user.username else actor, "denied", tool=tool, job_id=args["job_id"],
              details={"stage": "propose", "error": str(e), "args": args}, ts=as_of)
        conn.commit()  # keep the audit entry even though the caller's transaction will roll back
        raise
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO proposed_actions (tool, job_id, args, rationale, citations, proposed_by, proposed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING action_id""",
                    (tool, args["job_id"], Json(args), rationale, Json(citations or []), actor, as_of))
        action_id = cur.fetchone()["action_id"]
    audit(conn, user if actor == user.username else actor, "proposed", action_id=action_id, tool=tool,
          job_id=args["job_id"], details={"args": args, "rationale": rationale}, ts=as_of)
    return action_id


def _load_pending(conn, action_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM proposed_actions WHERE action_id = %s FOR UPDATE", (action_id,))
        row = cur.fetchone()
    if not row:
        raise ActionError(f"action {action_id} not found")
    if row["status"] != "pending":
        raise ActionError(f"action {action_id} is already {row['status']}")
    return dict(row)


def approve(conn, user: User, action_id: int, as_of: datetime | None = None, edited_args: dict | None = None) -> dict:
    """A person approves -> re-validate as that person -> execute -> log. Returns the result."""
    as_of = as_of or datetime.now()
    act = _load_pending(conn, action_id)
    args = {**act["args"], **(edited_args or {})}
    try:
        validate(conn, user, act["tool"], args, as_of)
    except PermissionDenied as e:
        audit(conn, user, "denied", action_id=action_id, tool=act["tool"], job_id=act["job_id"],
              details={"stage": "approve", "error": str(e)}, ts=as_of)
        conn.commit()
        raise
    except ActionError as e:
        _finish(conn, user, act, "failed", {"error": str(e)}, as_of)
        conn.commit()
        raise
    audit(conn, user, "approved", action_id=action_id, tool=act["tool"], job_id=act["job_id"],
          details={"args": args, "edited": bool(edited_args)}, ts=as_of)
    result = EXECUTORS[act["tool"]](conn, user, args, action_id, as_of)
    _finish(conn, user, act, "executed", result, as_of)
    return result


def reject(conn, user: User, action_id: int, reason: str = "", as_of: datetime | None = None) -> None:
    as_of = as_of or datetime.now()
    act = _load_pending(conn, action_id)
    job = queries.get_job(conn, user, act["job_id"], as_of)
    if job is None or not (user.is_manager or act["proposed_by"] == user.username):
        audit(conn, user, "denied", action_id=action_id, tool=act["tool"], job_id=act["job_id"],
              details={"stage": "reject"}, ts=as_of)
        conn.commit()
        raise PermissionDenied("you may not reject this action")
    _finish(conn, user, act, "rejected", {"reason": reason}, as_of, event="rejected")


def _finish(conn, user, act, status, result, as_of, event=None):
    with conn.cursor() as cur:
        cur.execute("""UPDATE proposed_actions SET status = %s, decided_by = %s, decided_at = %s, result = %s
                       WHERE action_id = %s""", (status, user.username, as_of, Json(result), act["action_id"]))
    audit(conn, user, event or status, action_id=act["action_id"], tool=act["tool"], job_id=act["job_id"],
          details=result, ts=as_of)


# --------------------------------------------------------------------------- the three tools
def _reassign(conn, user, args, action_id, as_of):
    with conn.cursor() as cur:
        cur.execute("SELECT technician_id FROM jobs WHERE job_id = %s", (args["job_id"],))
        old = cur.fetchone()["technician_id"]
        cur.execute("UPDATE jobs SET technician_id = %s WHERE job_id = %s",
                    (args["technician_id"].upper(), args["job_id"]))
    return {"job_id": args["job_id"], "from": old, "to": args["technician_id"].upper(), "reason": args["reason"]}


def _create_ticket(conn, user, args, action_id, as_of):
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(substring(ticket_id from 4)::int), 5000) + 1 AS n FROM tickets")
        tid = f"TK-{cur.fetchone()['n']}"
        cur.execute("SELECT technician_id FROM jobs WHERE job_id = %s", (args["job_id"],))
        tech = cur.fetchone()["technician_id"]
        cur.execute("""INSERT INTO tickets (ticket_id, job_id, technician_id, logged_at, category, notes, resolved)
                       VALUES (%s, %s, %s, %s, %s, %s, FALSE)""",
                    (tid, args["job_id"], tech, as_of, args["category"], args["notes"].strip()))
    return {"ticket_id": tid, "job_id": args["job_id"], "category": args["category"]}


def _draft_update(conn, user, args, action_id, as_of):
    with conn.cursor() as cur:
        cur.execute("SELECT customer_id FROM jobs WHERE job_id = %s", (args["job_id"],))
        cust = cur.fetchone()["customer_id"]
        cur.execute("""INSERT INTO customer_messages (job_id, customer_id, channel, body, action_id, approved_by, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING message_id""",
                    (args["job_id"], cust, args["channel"], args["message"].strip(), action_id, user.username, as_of))
        mid = cur.fetchone()["message_id"]
    return {"message_id": mid, "job_id": args["job_id"], "channel": args["channel"], "status": "ready_to_send"}


EXECUTORS = {"reassign_technician": _reassign, "create_ticket": _create_ticket,
             "draft_customer_update": _draft_update}


# --------------------------------------------------------------------------- planning
def _eta(job: dict, as_of: datetime) -> str:
    start = max(as_of, job["scheduled_at"]) + timedelta(minutes=30)
    start = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    fmt = lambda t: f"{t:%-I:%M %p}".lower()  # noqa: E731
    return f"{fmt(start)} to {fmt(end)}"


def customer_message(job: dict, as_of: datetime, technician_name: str | None = None) -> str:
    """SOP-CU §5 template."""
    reason = (job.get("reason") or "an earlier job running long").lower()
    first = (technician_name or job["technician"]).split()[0]
    return (f"Hi {job['customer']}, this is NorthStar Logistics about job {job['job_id']}. We are sorry, your "
            f"technician is running late because of {reason}. Your new arrival window is {_eta(job, as_of)}. "
            f"{first} will be with you. Reply to this message if that time does not work.")


def _open_pending(conn) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT tool, job_id FROM proposed_actions WHERE status = 'pending'")
        return {(r["tool"], r["job_id"]) for r in cur.fetchall()}


def plan_rules(conn, user: User, as_of: datetime, limit: int = 5, city: str | None = None) -> list[dict]:
    """Deterministic planner straight from the SOPs."""
    jobs = [j for j in queries.jobs_for_day(conn, user, as_of.date(), as_of, city=city)
            if j["is_delayed"] and j["status"] in queries.OPEN_STATUSES]
    jobs.sort(key=lambda r: (r["tier"] != "Gold", not r["is_breach"], r["priority"] != "High", -r["minutes_late"]))
    pending, taken, plans = _open_pending(conn), set(), []
    for job in jobs[:limit]:
        jid, reason = job["job_id"], job["reason"]
        new_tech = None
        if reason in (None, "Vehicle breakdown", "Parts missing"):
            free = queries.free_technicians(conn, job["city"], as_of.date(), exclude=taken | {job["technician_id"]})
            skill = {"Install": "Installer", "Repair": "Repair Tech"}.get(job["job_type"])
            free.sort(key=lambda t: (skill is not None and t["skill"] != skill))
            if free and ("reassign_technician", jid) not in pending:
                new_tech = free[0]
                taken.add(new_tech["technician_id"])
                why = reason.lower() if reason else "no update from the assigned technician"
                plans.append({"tool": "reassign_technician",
                              "args": {"job_id": jid, "technician_id": new_tech["technician_id"],
                                       "reason": f"{why}; {new_tech['name']} is free in {job['city']} (SOP-RA §2, §3)"},
                              "rationale": f"{jid} is {job['minutes_late']} min late ({reason or 'no reason logged'}). "
                                           f"{new_tech['name']} ({new_tech['technician_id']}) has no open jobs today.",
                              "citations": ["SOP-LD §5", "SOP-RA §2", "SOP-RA §3"]})
        if reason in ("Customer not home", "Address issue") and ("create_ticket", jid) not in pending:
            plans.append({"tool": "create_ticket",
                          "args": {"job_id": jid, "category": "Customer contact",
                                   "notes": f"Dispatch to call {job['customer']} to confirm access/address; "
                                            f"hold up to 60 min before rescheduling (SOP-LD §5)."},
                          "rationale": f"{reason} on {jid}: SOP-LD §5 says log a ticket and contact the customer.",
                          "citations": ["SOP-LD §5"]})
        if ("draft_customer_update", jid) not in pending:
            channel = "sms" if job["customer_phone"] else "email"
            plans.append({"tool": "draft_customer_update",
                          "args": {"job_id": jid, "channel": channel,
                                   "message": customer_message(job, as_of, new_tech["name"] if new_tech else None)},
                          "rationale": f"Every delayed job needs a customer update (SOP-LD §6)"
                                       + (" — Gold customer, also call them (SOP-CU §4)." if job["tier"] == "Gold" else "."),
                          "citations": ["SOP-LD §6", "SOP-CU §3", "SOP-CU §5"]})
    return plans


PLANNER_SYSTEM = """You are NorthStar's dispatch action agent. Propose actions for the delayed jobs given,
using ONLY the tools provided, following these SOP rules:
- SOP-LD §4 triage: Gold first, then SLA breaches, then High priority, then minutes late.
- SOP-LD §5: traffic/weather -> keep technician, update customer. Vehicle breakdown/parts missing -> reassign
  if a free technician exists. Customer not home/address issue -> create a 'Customer contact' ticket.
- SOP-RA §3: only reassign to a technician from the FREE list for the job's city.
- SOP-CU §3/§5: every delayed job gets a customer update that includes the job ID, an apology, the reason,
  a new ETA window and the technician's first name; never the technician's phone.
Your tool calls are proposals: a human approves each one before it runs. Propose at most 3 actions per job."""


def plan_claude(conn, user: User, as_of: datetime, client, limit: int = 5, city: str | None = None) -> list[dict]:
    jobs = [j for j in queries.jobs_for_day(conn, user, as_of.date(), as_of, city=city)
            if j["is_delayed"] and j["status"] in queries.OPEN_STATUSES][:limit]
    if not jobs:
        return []
    ctx = []
    for j in jobs:
        free = queries.free_technicians(conn, j["city"], as_of.date(), exclude={j["technician_id"]})[:5]
        ctx.append({k: (str(v) if isinstance(v, (datetime, date)) else v) for k, v in j.items()
                    if k not in ("customer_email",)} | {"free_technicians": free})
    calls = llm.tool_calls(client, PLANNER_SYSTEM,
                           f"Now: {as_of:%Y-%m-%d %H:%M}\nDelayed jobs:\n{json.dumps(ctx, default=str, indent=1)}",
                           TOOL_SPECS)
    return [{"tool": c["name"], "args": c["input"], "rationale": c["input"].get("reason", "Proposed by Claude"),
             "citations": []} for c in calls if c["name"] in TOOLS]


def suggest(conn, user: User, as_of: datetime | None = None, limit: int = 5, city: str | None = None,
            client=None) -> dict:
    """Plan, validate and store proposals. Returns {'created': [ids], 'skipped': [(plan, error)]}."""
    as_of = as_of or datetime.now().replace(second=0, microsecond=0)
    client = client if client is not None else llm.client()
    plans = plan_claude(conn, user, as_of, client, limit, city) if client else plan_rules(conn, user, as_of, limit, city)
    created, skipped = [], []
    for p in plans:
        try:
            created.append(propose(conn, user, p["tool"], p["args"], rationale=p["rationale"],
                                   citations=p["citations"], proposed_by="copilot", as_of=as_of))
        except (PermissionDenied, ActionError) as e:
            skipped.append((p, str(e)))
    return {"created": created, "skipped": skipped, "planner": "claude" if client else "rules"}


def list_actions(conn, user: User, status: str | None = None, limit: int = 100) -> list[dict]:
    """Actions on jobs this user can see."""
    sql = """SELECT a.* FROM proposed_actions a JOIN jobs j USING (job_id)
             WHERE (%(mgr)s OR j.technician_id = %(tech)s)"""
    params = {"mgr": user.is_manager, "tech": user.technician_id, "limit": limit}
    if status:
        sql += " AND a.status = %(status)s"
        params["status"] = status
    sql += " ORDER BY a.action_id DESC LIMIT %(limit)s"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def audit_entries(conn, user: User, limit: int = 200) -> list[dict]:
    """Managers see the whole log; technicians see entries they made or about their jobs."""
    sql = """SELECT l.* FROM audit_log l LEFT JOIN jobs j USING (job_id)
             WHERE (%(mgr)s OR l.actor = %(me)s OR j.technician_id = %(tech)s)
             ORDER BY l.log_id DESC LIMIT %(limit)s"""
    with conn.cursor() as cur:
        cur.execute(sql, {"mgr": user.is_manager, "me": user.username, "tech": user.technician_id, "limit": limit})
        return [dict(r) for r in cur.fetchall()]
