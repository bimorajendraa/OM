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
