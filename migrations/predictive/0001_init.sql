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

-- Cache data mentah untuk training (predictive.raw_*_cache) - salinan APA
-- ADANYA dari data_reader.get_events()/get_cycles()/get_failure_episodes(),
-- ditulis ulang penuh (TRUNCATE + INSERT) tiap refresh - lihat
-- predictive/raw_data_cache.py. Dipakai HANYA oleh training (build_dataset()
-- opsional lewat use_raw_cache=True), scoring TIDAK menyentuh tabel ini,
-- tetap baca langsung dari database operasional.
CREATE TABLE IF NOT EXISTS predictive.raw_events_cache (
    journey_id BIGINT PRIMARY KEY,
    item_identifier_clean TEXT NOT NULL,
    created_on TIMESTAMP NOT NULL,
    wo_type_clean TEXT,
    status_clean TEXT NOT NULL,
    item_type_clean TEXT NOT NULL,
    is_failure_onset BOOLEAN NOT NULL,
    place_canonical_clean TEXT,
    host_serial_code_clean TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictive.raw_cycles_cache (
    installation_cycle_id TEXT PRIMARY KEY,
    host_serial_code_clean TEXT NOT NULL,
    item_identifier_clean TEXT NOT NULL,
    installed_on TIMESTAMP NOT NULL,
    item_model_code_clean TEXT NOT NULL,
    installed_client_clean TEXT NOT NULL,
    failure_onset_on TIMESTAMP,
    cycle_end_on TIMESTAMP NOT NULL,
    cycle_end_reason TEXT NOT NULL,
    dataset_max_event_on TIMESTAMP NOT NULL,
    is_recon_verified_negative_eligible BOOLEAN NOT NULL,
    is_initial_model_cohort BOOLEAN NOT NULL,
    last_confirmable_observation_on TIMESTAMP NOT NULL,
    previous_cycle_lifetime_mean DOUBLE PRECISION,
    has_previous_cycle BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS predictive.raw_episodes_cache (
    onset_journey_id BIGINT PRIMARY KEY,
    item_identifier_clean TEXT NOT NULL,
    failure_onset_on TIMESTAMP NOT NULL,
    item_type_clean TEXT NOT NULL,
    item_model_code_clean TEXT NOT NULL,
    is_initial_model_cohort BOOLEAN NOT NULL
);
