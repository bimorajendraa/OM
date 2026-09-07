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

    alert_id BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (item_serial_code, inspection_seq)
);

CREATE INDEX IF NOT EXISTS ix_inspection_history_item_serial_code
    ON predictive.inspection_history (item_serial_code);

CREATE TABLE IF NOT EXISTS predictive.alert (
    alert_id BIGSERIAL PRIMARY KEY,

    terminal_serial_code TEXT,
    item_serial_code TEXT NOT NULL,
    inspection_seq INTEGER NOT NULL,
    prediction_id BIGINT REFERENCES predictive.item_prediction (prediction_id),

    opened_at TIMESTAMPTZ NOT NULL,
    opened_score DOUBLE PRECISION NOT NULL,

    status TEXT NOT NULL CHECK (status IN ('OPEN', 'RESOLVED')),

    resolved_at TIMESTAMPTZ,

    suppression_until TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_alert_item_serial_code ON predictive.alert (item_serial_code);

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open_per_item
    ON predictive.alert (split_part(item_serial_code, '-', 2))
    WHERE status = 'OPEN';

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_per_prediction
    ON predictive.alert (prediction_id)
    WHERE prediction_id IS NOT NULL;

ALTER TABLE predictive.inspection_history
    DROP CONSTRAINT IF EXISTS fk_inspection_alert;
ALTER TABLE predictive.inspection_history
    ADD CONSTRAINT fk_inspection_alert FOREIGN KEY (alert_id)
    REFERENCES predictive.alert (alert_id);
