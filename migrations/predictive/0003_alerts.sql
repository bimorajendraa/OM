CREATE TABLE IF NOT EXISTS predictive.alert (
    alert_id BIGSERIAL PRIMARY KEY,

    terminal_serial_code TEXT,
    item_id TEXT NOT NULL,
    host_serial_code TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS ix_alert_item ON predictive.alert (item_id);
CREATE INDEX IF NOT EXISTS ix_alert_host_serial_code ON predictive.alert (host_serial_code);

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open_per_episode
    ON predictive.alert (item_id, host_serial_code, inspection_seq)
    WHERE status = 'OPEN';

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_per_prediction
    ON predictive.alert (prediction_id)
    WHERE prediction_id IS NOT NULL;

ALTER TABLE predictive.inspection
    DROP CONSTRAINT IF EXISTS fk_inspection_alert;
ALTER TABLE predictive.inspection
    ADD CONSTRAINT fk_inspection_alert FOREIGN KEY (alert_id)
    REFERENCES predictive.alert (alert_id);
