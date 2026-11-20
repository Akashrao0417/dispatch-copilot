-- App tables: created IF NOT EXISTS and never dropped by the ETL,
-- so users, pending actions and the audit trail survive data reloads.
-- No foreign keys to jobs/technicians on purpose (those are rebuilt on every ETL run).

CREATE TABLE IF NOT EXISTS users (
    username       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('manager', 'technician')),
    technician_id  TEXT,                 -- set for technicians only
    CHECK ((role = 'technician') = (technician_id IS NOT NULL))
);

-- Every action the copilot (or a person) proposes. Nothing runs until status = 'approved'.
CREATE TABLE IF NOT EXISTS proposed_actions (
    action_id    SERIAL PRIMARY KEY,
    tool         TEXT NOT NULL CHECK (tool IN ('reassign_technician', 'create_ticket', 'draft_customer_update')),
    job_id       TEXT NOT NULL,
    args         JSONB NOT NULL,
    rationale    TEXT,
    citations    JSONB NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'executed', 'rejected', 'failed')),
    proposed_by  TEXT NOT NULL,          -- 'copilot' or a username
    proposed_at  TIMESTAMP NOT NULL,
    decided_by   TEXT,
    decided_at   TIMESTAMP,
    result       JSONB
);
CREATE INDEX IF NOT EXISTS proposed_actions_status_idx ON proposed_actions (status);

-- Append-only: who did what, when.
CREATE TABLE IF NOT EXISTS audit_log (
    log_id     SERIAL PRIMARY KEY,
    ts         TIMESTAMP NOT NULL,
    actor      TEXT NOT NULL,
    role       TEXT,
    event      TEXT NOT NULL CHECK (event IN ('proposed', 'approved', 'rejected', 'executed', 'failed', 'denied')),
    action_id  INTEGER,
    tool       TEXT,
    job_id     TEXT,
    details    JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);

-- Approved customer updates, ready to send (sending itself is out of scope).
CREATE TABLE IF NOT EXISTS customer_messages (
    message_id   SERIAL PRIMARY KEY,
    job_id       TEXT NOT NULL,
    customer_id  TEXT,
    channel      TEXT NOT NULL,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ready_to_send',
    action_id    INTEGER,
    approved_by  TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL
);
