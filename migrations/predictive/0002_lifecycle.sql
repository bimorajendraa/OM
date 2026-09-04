CREATE TABLE IF NOT EXISTS predictive.inspection (
    inspection_id BIGSERIAL PRIMARY KEY,

    item_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    inspection_seq INTEGER NOT NULL,

    alert_id BIGINT,
    external_event_id TEXT,

    performed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (cycle_id, inspection_seq),
    CONSTRAINT ux_inspection_external_event_id UNIQUE (external_event_id)
);

CREATE INDEX IF NOT EXISTS ix_inspection_item ON predictive.inspection (item_id);
CREATE INDEX IF NOT EXISTS ix_inspection_cycle ON predictive.inspection (cycle_id);
