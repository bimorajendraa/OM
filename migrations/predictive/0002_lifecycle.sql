CREATE TABLE IF NOT EXISTS predictive.inspection (
    inspection_id BIGSERIAL PRIMARY KEY,

    item_id TEXT NOT NULL,
    host_serial_code TEXT NOT NULL,
    inspection_seq INTEGER NOT NULL,

    alert_id BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (host_serial_code, inspection_seq)
);

CREATE INDEX IF NOT EXISTS ix_inspection_item ON predictive.inspection (item_id);
CREATE INDEX IF NOT EXISTS ix_inspection_host_serial_code ON predictive.inspection (host_serial_code);
