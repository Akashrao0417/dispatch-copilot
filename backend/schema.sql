-- NorthStar Logistics — clean operational model
DROP TABLE IF EXISTS tickets, jobs, technicians, customers CASCADE;

CREATE TABLE customers (
    customer_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT,            -- E.164 (+1XXXXXXXXXX) or NULL
    email           TEXT,
    city            TEXT NOT NULL,
    state           TEXT,
    tier            TEXT NOT NULL CHECK (tier IN ('Gold', 'Silver', 'Bronze')),
    crm_updated_at  TIMESTAMP
);

CREATE TABLE technicians (
    technician_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    home_city       TEXT NOT NULL,
    phone           TEXT,
    skill           TEXT,
    hire_date       DATE
);

CREATE TABLE jobs (
    job_id          TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers,
    technician_id   TEXT NOT NULL REFERENCES technicians,
    city            TEXT NOT NULL,
    job_type        TEXT,
    priority        TEXT,
    status          TEXT NOT NULL,
    created_at      TIMESTAMP,
    scheduled_at    TIMESTAMP NOT NULL,
    window_end_at   TIMESTAMP NOT NULL,  -- promised arrival window (scheduled + 2h)
    sla_due_at      TIMESTAMP NOT NULL,  -- contractual deadline by customer tier
    completed_at    TIMESTAMP
);
CREATE INDEX jobs_scheduled_idx ON jobs (scheduled_at);
CREATE INDEX jobs_tech_idx ON jobs (technician_id);

CREATE TABLE tickets (
    ticket_id       TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs,
    technician_id   TEXT REFERENCES technicians,
    logged_at       TIMESTAMP,
    category        TEXT,
    notes           TEXT,
    resolved        BOOLEAN
);
CREATE INDEX tickets_job_idx ON tickets (job_id);

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id   SERIAL PRIMARY KEY,
    ran_at   TIMESTAMP NOT NULL DEFAULT now(),
    stats    JSONB NOT NULL
);
