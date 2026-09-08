"""Penyimpanan model artifact (predictive.model_artifact) - menggantikan
filesystem models/failure/vN/. Schema predictive tetap satu-satunya tempat
partrisk menulis, konsisten dengan modul predictive/* lainnya.
"""

from __future__ import annotations

import io
import json
import os
import tempfile

import joblib
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.isotonic import IsotonicRegression

from partrisk.predictive import db


def next_version() -> str:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT model_version FROM predictive.model_artifact")
            rows = cur.fetchall()
    existing = [
        int(row[0][1:]) for row in rows
        if row[0][:1] == "v" and row[0][1:].isdigit()
    ]
    return f"v{max(existing, default=0) + 1}"


def current_version() -> str | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT model_version FROM predictive.model_artifact WHERE is_current")
            row = cur.fetchone()
    return row[0] if row else None


def set_current_version(model_version: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE predictive.model_artifact SET is_current = false WHERE is_current")
            cur.execute(
                "UPDATE predictive.model_artifact SET is_current = true WHERE model_version = %s",
                (model_version,),
            )
        conn.commit()


def save_version(
    model_version: str,
    model: CatBoostClassifier,
    calibrator: IsotonicRegression,
    fleet: pd.DataFrame,
    metadata: dict,
) -> None:
    """Simpan satu versi model utuh (model + calibrator + fleet snapshot +
    metadata) sebagai satu baris predictive.model_artifact. APPEND-ONLY -
    tidak ada UPDATE untuk model_version yang sudah ada."""
    descriptor, tmp_path = tempfile.mkstemp(suffix=".cbm")
    os.close(descriptor)
    try:
        # CatBoost save_model() cuma bisa menulis ke path file asli, tidak
        # ke buffer in-memory (diverifikasi) - jadi butuh satu file
        # sementara yang langsung dibaca lagi jadi bytes lalu dihapus.
        model.save_model(tmp_path)
        with open(tmp_path, "rb") as f:
            model_blob = f.read()
    finally:
        os.unlink(tmp_path)

    calibrator_buffer = io.BytesIO()
    joblib.dump(calibrator, calibrator_buffer)
    calibrator_blob = calibrator_buffer.getvalue()

    fleet_blob = fleet.to_csv(index=False).encode("utf-8")

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictive.model_artifact
                    (model_version, model_cbm, calibrator_joblib, fleet_snapshot_csv, metadata)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    model_version, model_blob, calibrator_blob, fleet_blob,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
        conn.commit()


def load_version(model_version: str) -> tuple[CatBoostClassifier, IsotonicRegression, dict]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT model_cbm, calibrator_joblib, metadata FROM predictive.model_artifact "
                "WHERE model_version = %s",
                (model_version,),
            )
            row = cur.fetchone()
    if row is None:
        raise FileNotFoundError(
            f"Model version {model_version!r} tidak ada di predictive.model_artifact."
        )
    model_blob, calibrator_blob, metadata = row

    model = CatBoostClassifier()
    model.load_model(blob=bytes(model_blob))
    calibrator = joblib.load(io.BytesIO(bytes(calibrator_blob)))
    return model, calibrator, metadata


def load_fleet_snapshot(model_version: str) -> pd.DataFrame:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fleet_snapshot_csv FROM predictive.model_artifact WHERE model_version = %s",
                (model_version,),
            )
            row = cur.fetchone()
    if row is None:
        raise FileNotFoundError(
            f"Model version {model_version!r} tidak ada di predictive.model_artifact."
        )
    return pd.read_csv(io.BytesIO(bytes(row[0])), dtype={"item_model_code_clean": str})


def artifact_size_bytes(model_version: str) -> int:
    """Total ukuran model+calibrator+fleet (bytes) - pengganti Path.stat()
    pada file lokal untuk gerbang ukuran artifact (mis. baseline-performance)."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT octet_length(model_cbm) + octet_length(calibrator_joblib) "
                "+ octet_length(fleet_snapshot_csv) FROM predictive.model_artifact "
                "WHERE model_version = %s",
                (model_version,),
            )
            row = cur.fetchone()
    if row is None:
        raise FileNotFoundError(
            f"Model version {model_version!r} tidak ada di predictive.model_artifact."
        )
    return int(row[0])


def list_versions() -> list[str]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT model_version FROM predictive.model_artifact ORDER BY created_at")
            return [row[0] for row in cur.fetchall()]
