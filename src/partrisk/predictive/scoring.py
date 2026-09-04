"""Menyimpan hasil batch scoring failure ke schema `predictive` -
`model_run` + `item_prediction` (append-only)."""

from __future__ import annotations

import logging

import pandas as pd

from partrisk.predictive import db

logger = logging.getLogger(__name__)


def start_run(model_version: str, feature_version: str | None = None) -> int:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictive.model_run
                    (model_version, feature_version, started_at, status)
                VALUES (%s, %s, now(), 'RUNNING')
                RETURNING run_id
                """,
                (model_version, feature_version),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
    return run_id


def complete_run(run_id: int, row_count: int) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE predictive.model_run
                SET status = 'SUCCEEDED', completed_at = now(), row_count = %s
                WHERE run_id = %s
                """,
                (row_count, run_id),
            )
        conn.commit()


def fail_run(run_id: int, error_message: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE predictive.model_run
                SET status = 'FAILED', completed_at = now(), error_message = %s
                WHERE run_id = %s
                """,
                (error_message[:2000], run_id),
            )
        conn.commit()


_PREDICTION_COLUMNS = (
    "run_id", "terminal_serial_code", "part_type", "item_id", "host_serial_code",
    "p30", "p60", "p90", "p120", "risk_level", "gate_flagged",
    "scored_at", "model_version", "feature_version",
)


def record_predictions(
    run_id: int,
    frame: pd.DataFrame,
    model_version: str,
    scored_at: pd.Timestamp,
    feature_version: str | None = None,
) -> int:
    """Tulis satu baris `item_prediction` per PART di `frame`. APPEND-ONLY."""
    rows = [
        (
            run_id,
            None if pd.isna(row.get("terminal_label")) else str(row["terminal_label"]),
            row.get("item_model_code"),
            row["item_id"],
            None if pd.isna(row.get("host_serial_code")) else str(row["host_serial_code"]),
            float(row["failure_probability_30d"]),
            float(row["failure_probability_60d"]),
            float(row["failure_probability_90d"]),
            float(row["failure_probability_120d"]),
            row["failure_risk_level"],
            bool(row["gate_flagged"]),
            scored_at.to_pydatetime(),
            model_version,
            feature_version,
        )
        for _, row in frame.iterrows()
    ]
    if not rows:
        return 0

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"""
                INSERT INTO predictive.item_prediction
                    ({", ".join(_PREDICTION_COLUMNS)})
                VALUES ({", ".join(["%s"] * len(_PREDICTION_COLUMNS))})
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def prediction_ids_for_run(run_id: int) -> dict[str, int]:
    """item_id -> prediction_id untuk satu run."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_id, prediction_id FROM predictive.item_prediction WHERE run_id = %s",
                (run_id,),
            )
            rows = cur.fetchall()
    return {item_id: prediction_id for item_id, prediction_id in rows}


def run_and_persist() -> dict:
    """Satu siklus scoring: skor seluruh PART aktif, simpan sebagai
    model_run + item_prediction baru, lalu evaluasi alert."""
    from partrisk.predictive import alerts as alert_engine
    from partrisk.serving import batch as serving_batch

    model_version = None
    run_id = None
    try:
        scores = serving_batch.score_active_parts(force_refresh=True)
        model_version = scores.model_version["failure"]
        run_id = start_run(model_version)
        scored_at = pd.Timestamp.now(tz="UTC")
        row_count = record_predictions(run_id, scores.frame, model_version, scored_at)
        complete_run(run_id, row_count)
        logger.info("model_run %s selesai: %d baris disimpan", run_id, row_count)
    except Exception as error:  # noqa: BLE001
        logger.exception("model_run gagal")
        if run_id is not None:
            fail_run(run_id, str(error))
        raise

    prediction_ids = prediction_ids_for_run(run_id)
    scores.frame["prediction_id"] = scores.frame["item_id"].map(prediction_ids)

    opened_alert_ids = alert_engine.evaluate_and_open(scores.frame, scored_at)
    if opened_alert_ids:
        logger.info("run_id %s membuka %d alert baru: %s", run_id, len(opened_alert_ids), opened_alert_ids)

    return {
        "run_id": run_id,
        "row_count": row_count,
        "model_version": model_version,
        "opened_alert_ids": opened_alert_ids,
    }
