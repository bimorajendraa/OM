from __future__ import annotations

import pandas as pd
import pytest

from partrisk.core import data_reader
from partrisk.predictive import alerts as alert_engine
from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db as predictive_db
from partrisk.predictive import inspections
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
            "item_id": "TEST-ITEM-001", "terminal_label": "T1", "item_model_code": "0000001",
            "failure_probability_30d": 0.1, "failure_probability_60d": 0.2,
            "failure_probability_90d": 0.3, "failure_probability_120d": 0.4,
            "failure_risk_level": "LOW", "gate_flagged": False,
        },
        {
            "item_id": "TEST-ITEM-002", "terminal_label": None, "item_model_code": "0000002",
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
                "SELECT item_id, terminal_serial_code, part_type, p30, risk_level, gate_flagged "
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
        "item_id": "TEST-ITEM-003", "terminal_label": None, "item_model_code": "0000003",
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


@needs_database
def test_prediction_ids_for_run_memetakan_item_id_ke_prediction_id(cleanup_run_ids):
    """docs/DECISIONS.md §32 - dipakai run_and_persist() menautkan
    alert.prediction_id ke baris item_prediction yang memicunya."""
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)

    frame = pd.DataFrame([
        {
            "item_id": "TEST-ITEM-010", "terminal_label": None, "item_model_code": "0000010",
            "failure_probability_30d": 0.1, "failure_probability_60d": 0.1,
            "failure_probability_90d": 0.1, "failure_probability_120d": 0.1,
            "failure_risk_level": "LOW", "gate_flagged": False,
        },
        {
            "item_id": "TEST-ITEM-011", "terminal_label": None, "item_model_code": "0000011",
            "failure_probability_30d": 0.9, "failure_probability_60d": 0.9,
            "failure_probability_90d": 0.9, "failure_probability_120d": 0.9,
            "failure_risk_level": "HIGH", "gate_flagged": True,
        },
    ])
    scored_at = pd.Timestamp.now(tz="UTC")
    scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)

    mapping = scoring.prediction_ids_for_run(run_id)

    assert set(mapping) == {"TEST-ITEM-010", "TEST-ITEM-011"}
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prediction_id FROM predictive.item_prediction "
                "WHERE run_id = %s AND item_id = 'TEST-ITEM-011'",
                (run_id,),
            )
            (expected_id,) = cur.fetchone()
    assert mapping["TEST-ITEM-011"] == expected_id


@pytest.fixture
def cleanup_item_lifecycle():
    touched_items: list[str] = []
    yield touched_items
    if not touched_items:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.inspection WHERE item_id = ANY(%s)", (touched_items,)
            )
        conn.commit()


@needs_database
@needs_models
def test_ensure_active_cycle_baca_dari_data_operasional(scorable_item):
    cycle = cycle_store.ensure_active_cycle(scorable_item)

    assert cycle["item_id"] == scorable_item
    assert cycle["is_active"] is True
    assert cycle["cycle_id"].startswith(scorable_item)


@needs_database
@needs_models
def test_ensure_active_cycle_idempotent(scorable_item):
    """Dibaca langsung dari data operasional (docs/DECISIONS.md §30, tidak
    ada lagi tabel mirror) - panggilan berulang untuk item yang sama harus
    selalu mengembalikan cycle aktif yang SAMA (deterministik)."""
    first = cycle_store.ensure_active_cycle(scorable_item)
    second = cycle_store.ensure_active_cycle(scorable_item)

    assert first["cycle_id"] == second["cycle_id"]
    assert first["is_active"] is True and second["is_active"] is True


@needs_database
def test_ensure_active_cycle_item_tidak_dikenal_ditolak():
    with pytest.raises(cycle_store.ItemNotInstalled):
        cycle_store.ensure_active_cycle("ITEM-TIDAK-PERNAH-ADA-XYZ")


@needs_database
@needs_models
def test_record_inspection_menaikkan_seq_dalam_cycle_yang_sama(
    scorable_item, cleanup_item_lifecycle
):
    cleanup_item_lifecycle.append(scorable_item)
    now = pd.Timestamp.now(tz="UTC")

    first = inspections.record_inspection(scorable_item, now)
    second = inspections.record_inspection(scorable_item, now)

    assert first["cycle_id"] == second["cycle_id"], (
        "minor repair tidak boleh membuka cycle baru - harus dalam cycle aktif yang sama"
    )
    assert second["inspection_seq"] == first["inspection_seq"] + 1


@pytest.fixture
def cleanup_alert_lifecycle():
    touched_items: list[str] = []
    yield touched_items
    if not touched_items:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.inspection WHERE item_id = ANY(%s)", (touched_items,)
            )
            cur.execute("DELETE FROM predictive.alert WHERE item_id = ANY(%s)", (touched_items,))
        conn.commit()


def _flagged_frame(item_id: str, score: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "item_id": item_id, "terminal_label": None, "item_model_code": "0000009",
        "failure_probability_30d": score, "gate_flagged": True,
    }])


@needs_database
def test_resolve_with_inspection_alert_tidak_ditemukan():
    with pytest.raises(alert_engine.AlertNotFound):
        alert_engine.resolve_with_inspection(999999999, pd.Timestamp.now(tz="UTC"))


@needs_database
@needs_models
def test_evaluate_and_open_lalu_resolve_lalu_ditolak_kalau_diulang(
    scorable_item, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)
    scored_at = pd.Timestamp.now(tz="UTC")

    opened_ids = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.5), scored_at)
    assert len(opened_ids) == 1
    alert_id = opened_ids[0]

    alert = alert_engine.get_alert(alert_id)
    assert alert["item_id"] == scorable_item
    assert alert["status"] == "OPEN"
    assert alert["opened_score"] == 0.5

    result = alert_engine.resolve_with_inspection(alert_id, pd.Timestamp.now(tz="UTC"))
    assert result["alert"]["status"] == "RESOLVED"
    assert result["inspection"]["alert_id"] == alert_id

    with pytest.raises(alert_engine.AlertNotOpen):
        alert_engine.resolve_with_inspection(alert_id, pd.Timestamp.now(tz="UTC"))


@needs_database
@needs_models
def test_evaluate_and_open_menautkan_alert_ke_prediction_id(
    scorable_item, cleanup_run_ids, cleanup_alert_lifecycle
):
    """docs/DECISIONS.md §32 - satu prediction menghasilkan NOL atau SATU
    alert; alert.prediction_id harus menunjuk balik ke baris item_prediction
    yang memicunya, ditegakkan UNIQUE(prediction_id)."""
    cleanup_alert_lifecycle.append(scorable_item)
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)
    scored_at = pd.Timestamp.now(tz="UTC")

    frame = _flagged_frame(scorable_item, 0.5)
    frame["failure_probability_60d"] = 0.5
    frame["failure_probability_90d"] = 0.5
    frame["failure_probability_120d"] = 0.5
    frame["failure_risk_level"] = "MEDIUM"
    scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)
    frame["prediction_id"] = frame["item_id"].map(scoring.prediction_ids_for_run(run_id))

    opened_ids = alert_engine.evaluate_and_open(frame, scored_at)
    assert len(opened_ids) == 1

    alert = alert_engine.get_alert(opened_ids[0])
    assert alert["prediction_id"] == int(frame["prediction_id"].iloc[0])


@needs_database
@needs_models
def test_evaluate_and_open_tidak_membuka_ulang_selama_masih_open(
    scorable_item, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)
    scored_at = pd.Timestamp.now(tz="UTC")

    first = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.5), scored_at)
    second = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.6), scored_at)

    assert len(first) == 1
    assert second == [], "alert yang masih OPEN tidak boleh dibuka ulang walau skor berubah"


@needs_database
@needs_models
def test_evaluate_and_open_suppressed_setelah_resolve_kecuali_emergency(
    scorable_item, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)
    scored_at = pd.Timestamp.now(tz="UTC")

    opened_ids = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.5), scored_at)
    alert_engine.resolve_with_inspection(opened_ids[0], pd.Timestamp.now(tz="UTC"))

    # skor naik sedikit - masih dalam masa suppression, BUKAN emergency jump.
    suppressed = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.55), scored_at)
    assert suppressed == [], "re-alert seharusnya ditahan selama masa suppression"

    # skor melonjak tajam (emergency override) - harus menembus suppression.
    emergency = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.95), scored_at)
    assert len(emergency) == 1, "lonjakan skor tajam harus membuka alert BARU walau masih disupresi"
    assert emergency[0] != opened_ids[0], "alert baru harus baris baru, bukan membuka lagi alert lama"


@pytest.fixture(scope="module")
def closed_cycle():
    """Cycle historis yang SUNGGUHAN sudah tertutup di data operasional
    (FAILURE/RETURNED/DISMANTLED), pada item yang SAAT INI juga punya cycle
    aktif (dipasang ulang) - dipakai membuktikan jalur auto-resolve
    (docs/DECISIONS.md §27) tanpa mengarang data operasional. Item tanpa
    cycle aktif sama sekali sengaja dikecualikan karena
    `resolve_with_inspection()` butuh `ensure_active_cycle()` berhasil
    (lihat test cycle-mismatch)."""
    from partrisk.core import data_reader

    all_cycles = data_reader.get_cycles()
    closed = all_cycles.loc[all_cycles["cycle_end_reason"] != "RIGHT_CENSORED_AT_DATA_END"]
    active_items = set(
        all_cycles.loc[
            all_cycles["cycle_end_reason"] == "RIGHT_CENSORED_AT_DATA_END", "item_identifier_clean"
        ]
    )
    candidate = closed.loc[closed["item_identifier_clean"].isin(active_items)]
    if candidate.empty:
        pytest.skip("tidak ada item dengan cycle tertutup DAN cycle aktif untuk diuji")
    row = candidate.iloc[0]
    return {
        "item_id": row["item_identifier_clean"],
        "cycle_id": row["installation_cycle_id"],
        "end_reason": row["cycle_end_reason"],
    }


@pytest.fixture
def cleanup_alert_ids():
    created: list[int] = []
    yield created
    if not created:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM predictive.alert WHERE alert_id = ANY(%s)", (created,))
        conn.commit()


def _insert_open_alert(item_id: str, cycle_id: str) -> int:
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictive.alert
                    (item_id, cycle_id, inspection_seq, opened_at, opened_score, status)
                VALUES (%s, %s, 0, now(), 0.5, 'OPEN')
                RETURNING alert_id
                """,
                (item_id, cycle_id),
            )
            alert_id = cur.fetchone()[0]
        conn.commit()
    return alert_id


@needs_database
def test_auto_resolve_closed_cycles_menutup_alert_pada_cycle_yang_sudah_berakhir(
    closed_cycle, cleanup_alert_ids
):
    alert_id = _insert_open_alert(closed_cycle["item_id"], closed_cycle["cycle_id"])
    cleanup_alert_ids.append(alert_id)

    resolved_ids = alert_engine.auto_resolve_closed_cycles([closed_cycle["item_id"]])

    assert alert_id in resolved_ids
    alert = alert_engine.get_alert(alert_id)
    assert alert["status"] == "RESOLVED"
    assert alert["resolution_reason"] == f"OPERATIONAL_CYCLE_CLOSED:{closed_cycle['end_reason']}"


@needs_database
def test_resolve_with_inspection_auto_resolve_alert_pada_cycle_lama(
    closed_cycle, cleanup_alert_ids
):
    """Kalau inspection diajukan untuk alert yang cycle-nya TERNYATA sudah
    tertutup di data operasional (item sudah pindah cycle), alert lama itu
    auto-resolved dulu (bukan AlertCycleMismatch mentah) - lihat WHY di
    resolve_with_inspection()."""
    alert_id = _insert_open_alert(closed_cycle["item_id"], closed_cycle["cycle_id"])
    cleanup_alert_ids.append(alert_id)

    with pytest.raises(alert_engine.AlertNotOpen):
        alert_engine.resolve_with_inspection(alert_id, pd.Timestamp.now(tz="UTC"))

    alert = alert_engine.get_alert(alert_id)
    assert alert["status"] == "RESOLVED"
    assert alert["resolution_reason"] == f"OPERATIONAL_CYCLE_CLOSED:{closed_cycle['end_reason']}"


@needs_database
def test_resolve_item_by_host_serial_code(scorable_item):
    """host_serial_code (format MODEL-PAIRINGCODE-REPAIRSEQ, docs §28) harus
    diresolve balik ke item_id internal yang sama dengan scorable_item."""
    with data_reader.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT host_serial_code FROM journal.t_item_journey "
                "WHERE UPPER(TRIM(item_pairing_code)) = %s AND host_serial_code IS NOT NULL "
                "ORDER BY created_on DESC LIMIT 1",
                (scorable_item,),
            )
            row = cur.fetchone()
    if row is None:
        pytest.skip(f"item {scorable_item} tidak punya host_serial_code di journal untuk diuji")

    resolved = data_reader.resolve_item_by_host_serial_code(row[0])
    assert resolved == scorable_item


@needs_database
def test_resolve_item_by_host_serial_code_tidak_ditemukan():
    assert data_reader.resolve_item_by_host_serial_code("TIDAK-ADA-SERIAL-CODE-SEPERTI-INI") is None


@needs_database
@needs_models
def test_resolve_by_item_dengan_alert_open_meresolve_alert(scorable_item, cleanup_alert_lifecycle):
    cleanup_alert_lifecycle.append(scorable_item)
    scored_at = pd.Timestamp.now(tz="UTC")
    opened_ids = alert_engine.evaluate_and_open(_flagged_frame(scorable_item, 0.5), scored_at)
    assert len(opened_ids) == 1

    result = alert_engine.resolve_by_item(scorable_item, pd.Timestamp.now(tz="UTC"))

    assert result["alert"] is not None
    assert result["alert"]["alert_id"] == opened_ids[0]
    assert result["alert"]["status"] == "RESOLVED"
    assert result["inspection"]["alert_id"] == opened_ids[0]


@needs_database
def test_resolve_by_item_tanpa_alert_open_tetap_mencatat_inspection(
    scorable_item, cleanup_item_lifecycle
):
    """docs/DECISIONS.md §25/§28: satu POST tetap berarti ada perbaikan
    walau item ini tidak sedang punya alert OPEN - inspection tetap
    dicatat, cuma tidak ada alert yang ikut ditutup."""
    cleanup_item_lifecycle.append(scorable_item)

    result = alert_engine.resolve_by_item(scorable_item, pd.Timestamp.now(tz="UTC"))

    assert result["alert"] is None
    assert result["inspection"]["item_id"] == scorable_item
    assert result["inspection"]["alert_id"] is None
