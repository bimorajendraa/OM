from __future__ import annotations

import pandas as pd
import pytest

from partrisk.predictive import db as predictive_db
from partrisk.predictive import scoring
from tests.conftest import needs_database


@pytest.fixture
def cleanup_run_ids():
    created: list[int] = []
    yield created
    if not created:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.item_prediction WHERE run_id = ANY(%s)", (created,)
            )
            cur.execute("DELETE FROM predictive.model_run WHERE run_id = ANY(%s)", (created,))
        conn.commit()


@needs_database
def test_start_run_lalu_complete_run(cleanup_run_ids):
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)
    scoring.complete_run(run_id, row_count=5)

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, row_count, completed_at FROM predictive.model_run WHERE run_id = %s",
                (run_id,),
            )
            status, row_count, completed_at = cur.fetchone()
    assert status == "SUCCEEDED"
    assert row_count == 5
    assert completed_at is not None


@needs_database
def test_fail_run_menandai_gagal_dengan_pesan(cleanup_run_ids):
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)
    scoring.fail_run(run_id, "koneksi database putus")

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, error_message FROM predictive.model_run WHERE run_id = %s",
                (run_id,),
            )
            status, error_message = cur.fetchone()
    assert status == "FAILED"
    assert "koneksi database putus" in error_message


@needs_database
def test_record_predictions_menulis_baris_sesuai_frame(cleanup_run_ids):
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)

    frame = pd.DataFrame([
        {
            "item_id": "TEST-ITEM-001", "terminal_id": "T1", "item_model_code": "0000001",
            "failure_probability_30d": 0.1, "failure_probability_60d": 0.2,
            "failure_probability_90d": 0.3, "failure_probability_120d": 0.4,
            "failure_risk_level": "LOW", "gate_flagged": False,
        },
        {
            "item_id": "TEST-ITEM-002", "terminal_id": None, "item_model_code": "0000002",
            "failure_probability_30d": 0.9, "failure_probability_60d": 0.95,
            "failure_probability_90d": 0.97, "failure_probability_120d": 0.99,
            "failure_risk_level": "HIGH", "gate_flagged": True,
        },
    ])
    scored_at = pd.Timestamp.now(tz="UTC")
    written = scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)
    assert written == 2

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_id, terminal_id, part_type, p30, risk_level, gate_flagged "
                "FROM predictive.item_prediction WHERE run_id = %s ORDER BY item_id",
                (run_id,),
            )
            rows = cur.fetchall()
    assert rows == [
        ("TEST-ITEM-001", "T1", "0000001", 0.1, "LOW", False),
        ("TEST-ITEM-002", None, "0000002", 0.9, "HIGH", True),
    ]


@needs_database
def test_record_predictions_append_only_tidak_menimpa_baris_lama(cleanup_run_ids):
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)

    frame = pd.DataFrame([{
        "item_id": "TEST-ITEM-003", "terminal_id": None, "item_model_code": "0000003",
        "failure_probability_30d": 0.5, "failure_probability_60d": 0.5,
        "failure_probability_90d": 0.5, "failure_probability_120d": 0.5,
        "failure_risk_level": "MEDIUM", "gate_flagged": False,
    }])
    scored_at = pd.Timestamp.now(tz="UTC")
    scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)
    scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM predictive.item_prediction "
                "WHERE run_id = %s AND item_id = 'TEST-ITEM-003'",
                (run_id,),
            )
            count = cur.fetchone()[0]
    assert count == 2, "dua kali record_predictions harus menghasilkan dua baris (append-only), bukan menimpa"
