CREATE TABLE IF NOT EXISTS predictive.item_cycle (
    cycle_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    cycle_no INTEGER NOT NULL,

    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,

    start_reason TEXT NOT NULL DEFAULT 'INSTALLED',

    end_reason TEXT,

    is_active BOOLEAN NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_item_cycle_item ON predictive.item_cycle (item_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_item_cycle_one_active
    ON predictive.item_cycle (item_id) WHERE is_active;


CREATE TABLE IF NOT EXISTS predictive.intervention (
    intervention_id BIGSERIAL PRIMARY KEY,

    item_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES predictive.item_cycle (cycle_id),
    intervention_seq INTEGER NOT NULL,

    alert_id BIGINT,

    performed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (cycle_id, intervention_seq)
);

CREATE INDEX IF NOT EXISTS ix_intervention_item ON predictive.intervention (item_id);
CREATE INDEX IF NOT EXISTS ix_intervention_cycle ON predictive.intervention (cycle_id);
