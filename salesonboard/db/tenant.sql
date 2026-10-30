-- One customer database. These tables live in the template database, and
-- every new customer database is created as a copy of it.
--
-- The sales_lines columns follow salesonboard/schema.py; a test checks that
-- they match. Safe to run more than once: every statement uses IF NOT EXISTS.


-- The canonical table: one row = one product line on one order.
CREATE TABLE IF NOT EXISTS sales_lines (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   text    NOT NULL,
    batch_id    text    NOT NULL,
    source_row  integer NOT NULL,        -- row in the uploaded file, for lineage
    order_id    text    NOT NULL,
    order_date  date    NOT NULL,
    product_id  text    NOT NULL,
    store_id    text,                    -- optional
    quantity    numeric NOT NULL,
    unit_price  numeric NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, source_row)        -- a file row is never loaded twice
);


-- Exactly one row: which tenant this database belongs to. The database writer
-- reads it before every load and stops if it does not match the batch's tenant.
CREATE TABLE IF NOT EXISTS tenant_meta (
    tenant_id       text PRIMARY KEY,
    schema_version  integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
