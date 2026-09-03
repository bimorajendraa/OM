from __future__ import annotations

import pandas as pd
import pytest

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db as predictive_db
from partrisk.predictive import interventions
from partrisk.predictive import scoring
from tests.conftest import needs_database, needs_models


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


@pytest.fixture
def cleanup_item_lifecycle():
    touched_items: list[str] = []
    yield touched_items
    if not touched_items:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.intervention WHERE item_id = ANY(%s)", (touched_items,)
            )
            cur.execute(
                "DELETE FROM predictive.item_cycle WHERE item_id = ANY(%s)", (touched_items,)
            )
        conn.commit()


@needs_database
@needs_models
def test_ensure_active_cycle_sinkron_dari_data_operasional(scorable_item, cleanup_item_lifecycle):
    cleanup_item_lifecycle.append(scorable_item)

    cycle = cycle_store.ensure_active_cycle(scorable_item)

    assert cycle["item_id"] == scorable_item
    assert cycle["is_active"] is True
    assert cycle["cycle_id"].startswith(scorable_item)


@needs_database
@needs_models
def test_ensure_active_cycle_idempotent(scorable_item, cleanup_item_lifecycle):
    """Sinkron dua kali TIDAK boleh menggandakan baris (upsert per cycle_id,
    bukan insert polos) - item boleh punya banyak cycle historis, tapi
    jumlah barisnya harus tetap sama lintas panggilan berulang, dan hanya
    satu yang aktif."""
    cleanup_item_lifecycle.append(scorable_item)

    first = cycle_store.ensure_active_cycle(scorable_item)
    second = cycle_store.ensure_active_cycle(scorable_item)

    assert first["cycle_id"] == second["cycle_id"]
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE is_active) "
                "FROM predictive.item_cycle WHERE item_id = %s",
                (scorable_item,),
            )
            total_after_first, active_after_first = cur.fetchone()

    cycle_store.ensure_active_cycle(scorable_item)
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE is_active) "
                "FROM predictive.item_cycle WHERE item_id = %s",
                (scorable_item,),
            )
            total_after_second, active_after_second = cur.fetchone()

    assert active_after_first == 1
    assert total_after_second == total_after_first, (
        "sinkron ulang tidak boleh menggandakan baris cycle yang sudah ada"
    )
    assert active_after_second == 1


@needs_database
def test_ensure_active_cycle_item_tidak_dikenal_ditolak():
    with pytest.raises(cycle_store.ItemNotInstalled):
        cycle_store.ensure_active_cycle("ITEM-TIDAK-PERNAH-ADA-XYZ")


@needs_database
@needs_models
def test_record_intervention_menaikkan_seq_dalam_cycle_yang_sama(
    scorable_item, cleanup_item_lifecycle
):
    cleanup_item_lifecycle.append(scorable_item)
    now = pd.Timestamp.now(tz="UTC")

    first, created_first = interventions.record_intervention(
        scorable_item, now, external_system="TEST", external_event_id="E1"
    )
    second, created_second = interventions.record_intervention(
        scorable_item, now,
        action_code="TIGHTENING", external_system="TEST", external_event_id="E2",
    )

    assert created_first is True and created_second is True
    assert first["cycle_id"] == second["cycle_id"], (
        "minor repair tidak boleh membuka cycle baru - harus dalam cycle aktif yang sama"
    )
    assert second["intervention_seq"] == first["intervention_seq"] + 1


@needs_database
@needs_models
def test_record_intervention_idempotent_lewat_external_event_id(
    scorable_item, cleanup_item_lifecycle
):
    cleanup_item_lifecycle.append(scorable_item)
    now = pd.Timestamp.now(tz="UTC")

    first, created_first = interventions.record_intervention(
        scorable_item, now, external_system="TEST", external_event_id="DUPLICATE"
    )
    retry, created_retry = interventions.record_intervention(
        scorable_item, now, external_system="TEST", external_event_id="DUPLICATE"
    )

    assert created_first is True
    assert created_retry is False
    assert retry["intervention_id"] == first["intervention_id"]

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM predictive.intervention "
                "WHERE external_system = 'TEST' AND external_event_id = 'DUPLICATE'"
            )
            assert cur.fetchone()[0] == 1
