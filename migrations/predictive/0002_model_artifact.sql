CREATE TABLE IF NOT EXISTS predictive.model_artifact (
    model_version TEXT PRIMARY KEY,
    model_cbm BYTEA NOT NULL,
    calibrator_joblib BYTEA NOT NULL,
    fleet_snapshot_csv BYTEA NOT NULL,
    metadata JSONB NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_artifact_one_current
    ON predictive.model_artifact (is_current) WHERE is_current;
