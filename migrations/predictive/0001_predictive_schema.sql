CREATE SCHEMA IF NOT EXISTS predictive;

CREATE TABLE IF NOT EXISTS predictive.model_run (
    run_id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL,
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
    item_serial_code TEXT NOT NULL,

    p30 DOUBLE PRECISION NOT NULL,
    p60 DOUBLE PRECISION NOT NULL,
    p90 DOUBLE PRECISION NOT NULL,
    p120 DOUBLE PRECISION NOT NULL,

    risk_level TEXT NOT NULL,
    gate_flagged BOOLEAN NOT NULL,
    alert_flagged BOOLEAN NOT NULL DEFAULT false,

    scored_at TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_item_prediction_run
    ON predictive.item_prediction (run_id);
CREATE INDEX IF NOT EXISTS ix_item_prediction_gate_flagged
    ON predictive.item_prediction (gate_flagged)
    WHERE gate_flagged;
CREATE INDEX IF NOT EXISTS ix_item_prediction_terminal
    ON predictive.item_prediction (terminal_serial_code);
CREATE INDEX IF NOT EXISTS ix_item_prediction_item_serial_code
    ON predictive.item_prediction (item_serial_code);

CREATE TABLE IF NOT EXISTS predictive.inspection_history (
    inspection_id BIGSERIAL PRIMARY KEY,

    item_serial_code TEXT NOT NULL,
    inspection_seq INTEGER NOT NULL,

    prediction_id BIGINT NOT NULL REFERENCES predictive.item_prediction (prediction_id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (item_serial_code, inspection_seq)
);

CREATE INDEX IF NOT EXISTS ix_inspection_history_item_serial_code
    ON predictive.inspection_history (item_serial_code);

CREATE UNIQUE INDEX IF NOT EXISTS ux_inspection_history_one_per_prediction
    ON predictive.inspection_history (prediction_id);
