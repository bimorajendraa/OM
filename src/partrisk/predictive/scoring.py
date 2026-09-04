"""Menyimpan hasil batch scoring failure (Q2) ke schema `predictive` -
`model_run` + `item_prediction` (append-only).

Dipanggil eksplisit (CLI `score-and-persist`, dipanggil scheduler eksternal
berkala) - BUKAN otomatis di setiap `serving.batch.score_active_parts()`,
supaya batch ad-hoc (API on-demand, CLI predict, test, golden-batch) tidak
ikut menulis baris ke riwayat prediksi setiap kali dipanggil.
"""

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
    "run_id", "terminal_id", "part_type", "item_id",
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
    """Tulis satu baris `item_prediction` per PART di `frame` (hasil
    `serving.batch.score_active_parts().frame`). APPEND-ONLY - tidak pernah
    UPDATE/DELETE baris lama, prediction_id sebelumnya tetap ada.

    Kolom `terminal_id` di sini diisi `frame["terminal_label"]` (serial code
    fisik terminal, docs/DECISIONS.md §28) - BUKAN `frame["terminal_id"]`
    (ID internal `terminal_inventory_item_id` yang dipakai jalur live/
    filtering di serving/batch.py, TIDAK berubah) - supaya aplikasi eksternal
    yang baca tabel ini bisa mengorelasikan terminal pakai kode yang sama
    dengan sistem mereka sendiri."""
    rows = [
        (
            run_id,
            None if pd.isna(row.get("terminal_label")) else str(row["terminal_label"]),
            row.get("item_model_code"),
            row["item_id"],
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


def run_and_persist() -> dict:
    """Satu siklus scoring: skor SELURUH PART aktif (force refresh, tidak
    pakai cache lama), simpan sebagai model_run + item_prediction baru.

    Dipanggil scheduler eksternal secara berkala (mis. cron) - lihat CLI
    `score-and-persist`. Kegagalan DI TENGAH scoring dicatat sebagai
    model_run FAILED, bukan diam-diam hilang.
    """
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

    # Evaluasi alert SETELAH model_run tercatat SUCCEEDED - kegagalan di sini
    # tidak mengubah status run (prediksi sudah aman tersimpan), tapi tetap
    # dilaporkan keras (raise), bukan ditelan diam-diam.
    opened_alert_ids = alert_engine.evaluate_and_open(scores.frame, scored_at)
    if opened_alert_ids:
        logger.info("run_id %s membuka %d alert baru: %s", run_id, len(opened_alert_ids), opened_alert_ids)

    return {
        "run_id": run_id,
        "row_count": row_count,
        "model_version": model_version,
        "opened_alert_ids": opened_alert_ids,
    }
