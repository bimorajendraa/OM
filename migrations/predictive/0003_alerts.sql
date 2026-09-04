CREATE TABLE IF NOT EXISTS predictive.alert (
    alert_id BIGSERIAL PRIMARY KEY,

    terminal_id TEXT,
    part_type TEXT,
    item_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES predictive.item_cycle (cycle_id),
    intervention_seq INTEGER NOT NULL,
    prediction_id BIGINT REFERENCES predictive.item_prediction (prediction_id),

    opened_at TIMESTAMPTZ NOT NULL,
    opened_score DOUBLE PRECISION NOT NULL,

    status TEXT NOT NULL CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED')),

    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    resolution_reason TEXT,

    suppression_until TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_alert_item ON predictive.alert (item_id);
CREATE INDEX IF NOT EXISTS ix_alert_cycle ON predictive.alert (cycle_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open_per_episode
    ON predictive.alert (item_id, cycle_id, intervention_seq)
    WHERE status = 'OPEN';

ALTER TABLE predictive.intervention
    DROP CONSTRAINT IF EXISTS fk_intervention_alert;
ALTER TABLE predictive.intervention
    ADD CONSTRAINT fk_intervention_alert FOREIGN KEY (alert_id)
    REFERENCES predictive.alert (alert_id);
