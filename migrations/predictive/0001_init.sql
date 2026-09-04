CREATE SCHEMA IF NOT EXISTS predictive;

CREATE TABLE IF NOT EXISTS predictive.model_run (
    run_id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL,
    feature_version TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    row_count INTEGER,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS predictive.item_prediction (
    prediction_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES predictive.model_run (run_id),

    terminal_serial_code TEXT,
    part_type TEXT,
    item_id TEXT NOT NULL,

    p30 DOUBLE PRECISION NOT NULL,
    p60 DOUBLE PRECISION NOT NULL,
    p90 DOUBLE PRECISION NOT NULL,
    p120 DOUBLE PRECISION NOT NULL,

    risk_level TEXT NOT NULL,
    gate_flagged BOOLEAN NOT NULL,

    scored_at TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT
);

CREATE INDEX IF NOT EXISTS ix_item_prediction_item_scored
    ON predictive.item_prediction (item_id, scored_at DESC);
CREATE INDEX IF NOT EXISTS ix_item_prediction_run
    ON predictive.item_prediction (run_id);
CREATE INDEX IF NOT EXISTS ix_item_prediction_gate_flagged
    ON predictive.item_prediction (gate_flagged)
    WHERE gate_flagged;
CREATE INDEX IF NOT EXISTS ix_item_prediction_terminal
    ON predictive.item_prediction (terminal_serial_code);
