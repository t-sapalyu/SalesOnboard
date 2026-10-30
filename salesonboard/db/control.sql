-- Control database: everything shared, and the audit trail for every upload.
-- Customer sales lines are NOT here; each customer has its own database.
--
-- Safe to run more than once: every statement uses IF NOT EXISTS.


-- One row per customer company.
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id      text PRIMARY KEY,                -- e.g. 'company1'
    name           text NOT NULL,
    username       text NOT NULL UNIQUE,            -- login name
    password_hash  text NOT NULL,                   -- never the plain password
    database_name  text NOT NULL UNIQUE,            -- this customer's own database
    created_at     timestamptz NOT NULL DEFAULT now()
);


-- One row per upload. The status says which step the batch is at, so the
-- web page can reopen at the right step after the browser is closed.
CREATE TABLE IF NOT EXISTS batches (
    batch_id             text PRIMARY KEY,
    tenant_id            text NOT NULL REFERENCES tenants (tenant_id),
    status               text NOT NULL,
    file_name            text NOT NULL,             -- name the customer uploaded
    file_path            text NOT NULL,             -- where we saved it on disk
    file_hash            text NOT NULL,             -- sha256 of the file
    row_count            integer,                   -- data rows in the file
    rows_loaded          integer,
    rows_dropped         integer,
    profile              jsonb,                     -- the profiler's FileProfile
    cost_eur             numeric(10, 6) NOT NULL DEFAULT 0,  -- LLM spend so far
    investigator_rounds  integer NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- One active batch per customer: a second upload is rejected until the first
-- one is loaded, stopped, or sent to support. Different customers never
-- block each other, because the index is per tenant_id.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_batch_per_tenant
    ON batches (tenant_id)
    WHERE status NOT IN ('loaded', 'stopped', 'support');


-- Every version of the plan, and every button press, with who did it and when.
CREATE TABLE IF NOT EXISTS plan_versions (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id    text NOT NULL REFERENCES batches (batch_id),
    version     integer NOT NULL,                   -- 1, 2, 3 ... per batch
    plan        jsonb NOT NULL,                     -- the TransformPlan as JSON
    created_by  text NOT NULL,                      -- mapping_agent, investigator, human
    action      text NOT NULL,                      -- e.g. proposed, approved, fix_applied
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, version)
);


-- Each rule's result for each validation run of a batch.
CREATE TABLE IF NOT EXISTS validation_results (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id      text NOT NULL REFERENCES batches (batch_id),
    run           integer NOT NULL,                 -- 1 for the first run, 2 after a fix ...
    rule          text NOT NULL,                    -- R1, R2 ...
    passed        boolean NOT NULL,
    failing_rows  jsonb NOT NULL DEFAULT '[]',      -- list of source_row numbers
    message       text,
    created_at    timestamptz NOT NULL DEFAULT now()
);


-- Every LLM call: what it cost and how long it took.
CREATE TABLE IF NOT EXISTS llm_calls (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id       text NOT NULL REFERENCES batches (batch_id),
    agent          text NOT NULL,                   -- mapping_agent or investigator
    model          text NOT NULL,
    input_tokens   integer NOT NULL,
    output_tokens  integer NOT NULL,
    cost_eur       numeric(10, 6) NOT NULL,
    latency_ms     integer NOT NULL,
    tool_calls     jsonb NOT NULL DEFAULT '[]',     -- names and arguments of tools used
    created_at     timestamptz NOT NULL DEFAULT now()
);


-- Tickets from the "Send to support" button, and from automatic stops.
-- In Phase 1 nothing is sent anywhere; the team reads this table.
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- shown to the customer
    batch_id    text NOT NULL REFERENCES batches (batch_id),
    tenant_id   text NOT NULL REFERENCES tenants (tenant_id),
    step        text NOT NULL,                      -- the batch status when it was created
    reason      text NOT NULL,                      -- customer, cost_limit, round_limit, invalid_output
    note        text,                               -- what the customer wrote, if anything
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
