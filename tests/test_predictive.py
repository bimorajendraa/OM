from __future__ import annotations

import pandas as pd
import pytest

from partrisk.core import data_reader
from partrisk.predictive import alerts as alert_engine
from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db as predictive_db
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
            "item_id": "TEST-ITEM-001", "terminal_label": "T1",
            "host_serial_code": "0000001-TEST-ITEM-001-00",
            "failure_probability_30d": 0.1, "failure_probability_60d": 0.2,
            "failure_probability_90d": 0.3, "failure_probability_120d": 0.4,
            "failure_risk_level": "LOW", "gate_flagged": False,
        },
        {
            "item_id": "TEST-ITEM-002", "terminal_label": None,
            "host_serial_code": "0000002-TEST-ITEM-002-00",
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
                "SELECT item_serial_code, terminal_serial_code, p30, risk_level, gate_flagged "
                "FROM predictive.item_prediction WHERE run_id = %s ORDER BY item_serial_code",
                (run_id,),
            )
            rows = cur.fetchall()
    assert rows == [
        ("0000001-TEST-ITEM-001-00", "T1", 0.1, "LOW", False),
        ("0000002-TEST-ITEM-002-00", None, 0.9, "HIGH", True),
    ]


@needs_database
def test_record_predictions_append_only_tidak_menimpa_baris_lama(cleanup_run_ids):
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)

    frame = pd.DataFrame([{
        "item_id": "TEST-ITEM-003", "terminal_label": None,
        "host_serial_code": "0000003-TEST-ITEM-003-00",
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
                "WHERE run_id = %s AND item_serial_code = '0000003-TEST-ITEM-003-00'",
                (run_id,),
            )
            count = cur.fetchone()[0]
    assert count == 2, "dua kali record_predictions harus menghasilkan dua baris (append-only), bukan menimpa"


def _valid_prediction_row(item_id: str = "TEST-ITEM-GUARD") -> dict:
    return {
        "item_id": item_id, "terminal_label": None,
        "host_serial_code": f"0000000-{item_id}-00",
        "failure_probability_30d": 0.1, "failure_probability_60d": 0.2,
        "failure_probability_90d": 0.3, "failure_probability_120d": 0.4,
        "failure_risk_level": "LOW", "gate_flagged": False,
    }


def test_record_predictions_menolak_frame_kosong():
    with pytest.raises(RuntimeError, match="kosong"):
        scoring.record_predictions(1, pd.DataFrame(), "test-model-v0", pd.Timestamp.now(tz="UTC"))


def test_record_predictions_menolak_item_id_duplikat():
    frame = pd.DataFrame([_valid_prediction_row(), _valid_prediction_row()])
    with pytest.raises(RuntimeError, match="duplikat"):
        scoring.record_predictions(1, frame, "test-model-v0", pd.Timestamp.now(tz="UTC"))


def test_record_predictions_menolak_probabilitas_nan():
    row = _valid_prediction_row()
    row["failure_probability_60d"] = float("nan")
    frame = pd.DataFrame([row])
    with pytest.raises(RuntimeError, match="NaN"):
        scoring.record_predictions(1, frame, "test-model-v0", pd.Timestamp.now(tz="UTC"))


def test_record_predictions_menolak_host_serial_code_kosong():
    row = _valid_prediction_row()
    row["host_serial_code"] = None
    frame = pd.DataFrame([row])
    with pytest.raises(RuntimeError, match="host_serial_code"):
        scoring.record_predictions(1, frame, "test-model-v0", pd.Timestamp.now(tz="UTC"))


@needs_database
def test_db_menolak_item_serial_code_null_di_item_prediction(cleanup_run_ids):
    """docs/DECISIONS.md §41 - kontrak item_serial_code NOT NULL ditegakkan
    DUA lapis: guard Python (test di atas) DAN constraint database - kalau
    guard Python suatu saat dilewati/bug, database tetap menolak."""
    run_id = scoring.start_run("test-model-v0")
    cleanup_run_ids.append(run_id)

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception, match="item_serial_code|not-null|null value"):
                cur.execute(
                    """
                    INSERT INTO predictive.item_prediction
                        (run_id, item_serial_code, p30, p60, p90, p120, risk_level,
                         gate_flagged, scored_at, model_version)
                    VALUES (%s, NULL, 0.1, 0.1, 0.1, 0.1, 'LOW', FALSE, now(), 'test-model-v0')
                    """,
                    (run_id,),
                )
        conn.rollback()


@needs_database
@needs_models
def test_run_and_persist_selesai_succeeded_setelah_alert_diproses(cleanup_run_ids):
    """model_run.status TIDAK boleh SUCCEEDED sebelum alert_flagged ikut
    terhitung - lihat WHY di scoring.py::run_and_persist()."""
    result = scoring.run_and_persist()
    cleanup_run_ids.append(result["run_id"])

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, row_count, completed_at FROM predictive.model_run WHERE run_id = %s",
                (result["run_id"],),
            )
            status, row_count, completed_at = cur.fetchone()
    assert status == "SUCCEEDED"
    assert completed_at is not None
    assert row_count == result["row_count"]
    assert isinstance(result["alert_flagged_prediction_ids"], list)


@needs_database
@needs_models
def test_ensure_active_cycle_baca_dari_data_operasional(scorable_item):
    cycle = cycle_store.ensure_active_cycle(scorable_item)

    assert cycle["item_id"] == scorable_item
    assert cycle["is_active"] is True
    assert scorable_item in cycle["cycle_id"], (
        "cycle_id sekarang = host_serial_code (docs/DECISIONS.md §38), "
        "item_id muncul sebagai segmen tengah MODEL-item_id-REPAIRSEQ"
    )


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


@pytest.fixture
def cleanup_alert_lifecycle():
    """Bersihkan seluruh jejak item_prediction/inspection_history/model_run
    sintetis dibuat test alert lifecycle, dikelompokkan per item_id
    (pairing code)."""
    touched_items: list[str] = []
    yield touched_items
    if not touched_items:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.inspection_history "
                "WHERE split_part(item_serial_code, '-', 2) = ANY(%s)", (touched_items,)
            )
            cur.execute(
                "SELECT DISTINCT run_id FROM predictive.item_prediction "
                "WHERE split_part(item_serial_code, '-', 2) = ANY(%s)", (touched_items,)
            )
            run_ids = [row[0] for row in cur.fetchall()]
            cur.execute(
                "DELETE FROM predictive.item_prediction "
                "WHERE split_part(item_serial_code, '-', 2) = ANY(%s)", (touched_items,)
            )
            if run_ids:
                # run_id BISA dipakai bersama run scoring penuh (mis. item test
                # ini kebetulan ikut ter-scan score-and-persist sungguhan) -
                # HANYA hapus model_run yang sudah yatim (tidak ada item_prediction
                # lain yang masih merujuknya), jangan pernah hapus run yang masih
                # dipakai baris lain (FK violation, atau lebih parah - menghapus
                # riwayat scoring sungguhan).
                cur.execute(
                    "DELETE FROM predictive.model_run WHERE run_id = ANY(%s) "
                    "AND NOT EXISTS (SELECT 1 FROM predictive.item_prediction "
                    "WHERE item_prediction.run_id = model_run.run_id)",
                    (run_ids,),
                )
        conn.commit()


def _prediction_row(item_id: str, host_serial_code: str, score: float, gate_flagged: bool) -> dict:
    return {
        "item_id": item_id, "terminal_label": None,
        "host_serial_code": host_serial_code,
        "failure_probability_30d": score, "failure_probability_60d": score,
        "failure_probability_90d": score, "failure_probability_120d": score,
        "failure_risk_level": "HIGH" if gate_flagged else "LOW",
        "gate_flagged": gate_flagged,
    }


def _score_and_flag(
    item_id: str, host_serial_code: str, score: float, gate_flagged: bool = True,
    scored_at: pd.Timestamp | None = None,
) -> dict:
    """Simulasi satu siklus scoring untuk SATU item lewat
    scoring.record_predictions() (jalur asli, bukan lagi evaluate_and_open()
    terpisah) - kembalikan prediction_id + alert_flagged hasilnya.

    complete_run() WAJIB dipanggil di sini - open_alerts_by_item()/
    get_alert()/_has_open_alert() sekarang cuma menganggap prediction dari
    model_run berstatus SUCCEEDED sebagai alert yang sah (run RUNNING/FAILED
    tidak valid, lihat alerts.py::_run_succeeded())."""
    run_id = scoring.start_run("test-model-v0")
    scored_at = scored_at or pd.Timestamp.now(tz="UTC")
    frame = pd.DataFrame([_prediction_row(item_id, host_serial_code, score, gate_flagged)])
    row_count = scoring.record_predictions(run_id, frame, "test-model-v0", scored_at)
    scoring.complete_run(run_id, row_count)
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prediction_id, alert_flagged FROM predictive.item_prediction "
                "WHERE run_id = %s AND item_serial_code = %s",
                (run_id, host_serial_code),
            )
            prediction_id, alert_flagged = cur.fetchone()
    return {"run_id": run_id, "prediction_id": prediction_id, "alert_flagged": alert_flagged}


@needs_database
def test_resolve_with_inspection_alert_tidak_ditemukan():
    with pytest.raises(alert_engine.AlertNotFound):
        alert_engine.resolve_with_inspection(999999999)


@needs_database
@needs_models
def test_gate_flagged_false_tidak_menghasilkan_alert(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    """gate_flagged=False (tier RANKED) tidak boleh menghasilkan
    alert_flagged=True - docs/DECISIONS.md §45."""
    cleanup_alert_lifecycle.append(scorable_item)

    result = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.9, gate_flagged=False)
    assert result["alert_flagged"] is False
    assert alert_engine.open_alerts_by_item([scorable_item]) == {}


@needs_database
@needs_models
def test_alert_flagged_lalu_resolve_lalu_ditolak_kalau_diulang(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)

    opened = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    assert opened["alert_flagged"] is True
    prediction_id = opened["prediction_id"]

    alert = alert_engine.get_alert(prediction_id)
    assert alert_engine._pairing_code(alert["item_serial_code"]) == scorable_item
    assert alert["p30"] == 0.5

    result = alert_engine.resolve_with_inspection(prediction_id)
    assert result["alert"]["prediction_id"] == prediction_id
    assert result["inspection"]["prediction_id"] == prediction_id

    with pytest.raises(alert_engine.AlertNotOpen):
        alert_engine.resolve_with_inspection(prediction_id)


@needs_database
@needs_models
def test_alert_flagged_tidak_true_lagi_selama_masih_open(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)

    first = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    second = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.6)

    assert first["alert_flagged"] is True
    assert second["alert_flagged"] is False, (
        "alert yang masih terbuka (belum di-inspect) tidak boleh diflag lagi walau skor berubah"
    )


@needs_database
@needs_models
def test_alert_flagged_suppressed_setelah_resolve_kecuali_emergency(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)

    opened = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    alert_engine.resolve_with_inspection(opened["prediction_id"])

    # skor naik sedikit - masih dalam masa suppression, BUKAN emergency jump.
    suppressed = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.55)
    assert suppressed["alert_flagged"] is False, "re-alert seharusnya ditahan selama masa suppression"

    # skor melonjak tajam (emergency override) - harus menembus suppression.
    emergency = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.95)
    assert emergency["alert_flagged"] is True, "lonjakan skor tajam harus menembus suppression"
    assert emergency["prediction_id"] != opened["prediction_id"], (
        "alert baru harus baris baru, bukan membuka lagi alert lama"
    )


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
    candidate = closed.loc[
        closed["item_identifier_clean"].isin(active_items)
        & closed["host_serial_code_clean"].notna()
    ]
    if candidate.empty:
        pytest.skip("tidak ada item dengan cycle tertutup DAN cycle aktif untuk diuji")
    row = candidate.iloc[0]
    return {
        "item_id": row["item_identifier_clean"],
        "cycle_id": row["host_serial_code_clean"],
        "end_reason": row["cycle_end_reason"],
    }


@pytest.fixture
def cleanup_prediction_ids():
    created: list[int] = []
    yield created
    if not created:
        return
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictive.inspection_history WHERE prediction_id = ANY(%s)", (created,)
            )
            cur.execute(
                "SELECT DISTINCT run_id FROM predictive.item_prediction WHERE prediction_id = ANY(%s)",
                (created,),
            )
            run_ids = [row[0] for row in cur.fetchall()]
            cur.execute("DELETE FROM predictive.item_prediction WHERE prediction_id = ANY(%s)", (created,))
            if run_ids:
                cur.execute(
                    "DELETE FROM predictive.model_run WHERE run_id = ANY(%s) "
                    "AND NOT EXISTS (SELECT 1 FROM predictive.item_prediction "
                    "WHERE item_prediction.run_id = model_run.run_id)",
                    (run_ids,),
                )
        conn.commit()


def _insert_flagged_prediction(
    item_serial_code: str, score: float = 0.5, scored_at: pd.Timestamp | None = None,
    run_status: str = "SUCCEEDED",
) -> int:
    """INSERT langsung (bypass compute_alert_flagged) - dipakai simulasi
    skenario yang butuh baris alert_flagged=true SUDAH ada di DB terlepas
    dari histori suppression (mis. cycle tertutup, atau duplikat sengaja).
    `run_status` dipakai menguji bahwa prediction dari run RUNNING/FAILED
    TIDAK dianggap alert yang sah (alerts.py::_run_succeeded())."""
    scored_at = scored_at or pd.Timestamp.now(tz="UTC")
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO predictive.model_run (model_version, started_at, status) "
                "VALUES ('test-model-v0', now(), %s) RETURNING run_id",
                (run_status,),
            )
            run_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO predictive.item_prediction
                    (run_id, item_serial_code, p30, p60, p90, p120, risk_level,
                     gate_flagged, alert_flagged, scored_at, model_version)
                VALUES (%s, %s, %s, %s, %s, %s, 'MEDIUM', TRUE, TRUE, %s, 'test-model-v0')
                RETURNING prediction_id
                """,
                (run_id, item_serial_code, score, score, score, score, scored_at.to_pydatetime()),
            )
            prediction_id = cur.fetchone()[0]
        conn.commit()
    return prediction_id


@needs_database
def test_open_alerts_by_item_pakai_yang_terbaru_kalau_ada_duplikat(cleanup_prediction_ids):
    """Jalur normal (compute_alert_flagged) tidak pernah membuat dua
    prediction alert_flagged=true yang sama-sama belum di-inspect untuk
    item yang sama (lihat test_alert_flagged_tidak_true_lagi_selama_masih_open).
    Tapi kalau itu tetap terjadi (mis. race, atau insert manual),
    open_alerts_by_item() harus tetap pakai yang scored_at TERBARU, bukan
    crash atau ambigu."""
    older = _insert_flagged_prediction(
        "TESTMODEL-DUPITEM-01", score=0.5, scored_at=pd.Timestamp("2026-01-01", tz="UTC")
    )
    newer = _insert_flagged_prediction(
        "TESTMODEL-DUPITEM-02", score=0.9, scored_at=pd.Timestamp("2026-02-01", tz="UTC")
    )
    cleanup_prediction_ids.extend([older, newer])

    result = alert_engine.open_alerts_by_item(["DUPITEM"])
    assert result["DUPITEM"]["prediction_id"] == newer


@needs_database
@pytest.mark.parametrize("run_status", ["RUNNING", "FAILED"])
def test_prediction_dari_run_belum_selesai_tidak_dianggap_alert_sah(
    run_status, cleanup_prediction_ids
):
    """record_predictions() commit SEBELUM complete_run() selesai - baris
    item_prediction alert_flagged=true bisa saja berasal dari run yang
    ujungnya RUNNING (proses crash sebelum sempat complete_run) atau FAILED.
    open_alerts_by_item()/get_alert() WAJIB mengabaikan prediction seperti
    ini (alerts.py::_run_succeeded())."""
    prediction_id = _insert_flagged_prediction(
        "TESTMODEL-BELUMSELESAI-01", run_status=run_status
    )
    cleanup_prediction_ids.append(prediction_id)

    assert alert_engine.get_alert(prediction_id) is None
    assert alert_engine.open_alerts_by_item(["BELUMSELESAI"]) == {}


@needs_database
@pytest.mark.parametrize("run_status", ["RUNNING", "FAILED"])
def test_prediction_dari_run_belum_selesai_tidak_menahan_scoring_berikutnya(
    run_status, cleanup_prediction_ids
):
    """_has_open_alert() (dipakai compute_alert_flagged() saat scoring
    berikutnya) juga TIDAK boleh menganggap prediction dari run
    RUNNING/FAILED sebagai 'sudah ada alert terbuka' - kalau tidak, item
    itu tidak akan pernah bisa di-flag lagi walau run yang bermasalah tidak
    pernah menghasilkan alert yang sungguhan valid."""
    item_serial_code = "TESTMODEL-BELUMSELESAI2-01"
    prediction_id = _insert_flagged_prediction(item_serial_code, run_status=run_status)
    cleanup_prediction_ids.append(prediction_id)

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            assert alert_engine._has_open_alert(cur, item_serial_code) is False


@needs_database
def test_auto_resolve_closed_cycles_menutup_alert_pada_cycle_yang_sudah_berakhir(
    closed_cycle, cleanup_prediction_ids
):
    prediction_id = _insert_flagged_prediction(closed_cycle["cycle_id"])
    cleanup_prediction_ids.append(prediction_id)

    resolved_ids = alert_engine.auto_resolve_closed_cycles([closed_cycle["item_id"]])

    assert prediction_id in resolved_ids
    assert alert_engine.open_alerts_by_item([closed_cycle["item_id"]]) == {}


@needs_database
def test_resolve_with_inspection_auto_resolve_alert_pada_cycle_lama(
    closed_cycle, cleanup_prediction_ids
):
    """Kalau inspection diajukan untuk alert yang cycle-nya TERNYATA sudah
    tertutup di data operasional (item sudah pindah cycle), alert lama itu
    auto-resolved dulu (bukan AlertCycleMismatch mentah) - lihat WHY di
    resolve_with_inspection()."""
    prediction_id = _insert_flagged_prediction(closed_cycle["cycle_id"])
    cleanup_prediction_ids.append(prediction_id)

    with pytest.raises(alert_engine.AlertNotOpen):
        alert_engine.resolve_with_inspection(prediction_id)

    assert alert_engine.open_alerts_by_item([closed_cycle["item_id"]]) == {}


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
def test_resolve_by_item_dengan_alert_open_meresolve_alert(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    cleanup_alert_lifecycle.append(scorable_item)
    opened = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    assert opened["alert_flagged"] is True

    result = alert_engine.resolve_by_item(
        scorable_item, scorable_item_host_serial_code
    )

    assert result["alert"] is not None
    assert result["alert"]["prediction_id"] == opened["prediction_id"]
    assert result["inspection"]["prediction_id"] == opened["prediction_id"]


@needs_database
def test_resolve_by_item_tanpa_alert_open_ditolak(
    scorable_item, scorable_item_host_serial_code
):
    """Keputusan user: endpoint ini HANYA untuk merespons alert yang sudah
    dibuka model - item tanpa alert OPEN harus ditolak (`NoOpenAlert`),
    bukan diam-diam dicatat sebagai inspection berdiri sendiri (SUPERSEDED
    dari docs/DECISIONS.md §25)."""
    with pytest.raises(alert_engine.NoOpenAlert) as excinfo:
        alert_engine.resolve_by_item(scorable_item, scorable_item_host_serial_code)

    assert excinfo.value.item_id == scorable_item


@needs_database
@needs_models
def test_resolve_by_item_dua_episode_alert_berturutan_hasilkan_dua_inspection(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    """Dua episode alert berturutan pada item yang sama (buka -> resolve ->
    re-alert -> resolve) masing-masing harus menghasilkan baris inspection
    SENDIRI (inspection_seq naik) - resolve tidak boleh menelan/
    menggabungkan episode yang berbeda. Alert kedua sengaja diberi skor
    jauh lebih tinggi supaya menembus suppression lewat emergency override."""
    cleanup_alert_lifecycle.append(scorable_item)

    opened_a = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    assert opened_a["alert_flagged"] is True
    first = alert_engine.resolve_by_item(scorable_item, scorable_item_host_serial_code)

    opened_b = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.95)
    assert opened_b["alert_flagged"] is True, "skor jauh lebih tinggi harus menembus suppression (emergency override)"
    second = alert_engine.resolve_by_item(scorable_item, scorable_item_host_serial_code)

    assert first["inspection"]["inspection_id"] != second["inspection"]["inspection_id"]
    assert second["inspection"]["inspection_seq"] == first["inspection"]["inspection_seq"] + 1


@needs_database
@needs_models
def test_resolve_by_item_host_serial_code_current_berhasil(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    """Kasus 1 - host_serial_code CURRENT (cycle aktif) berhasil resolve."""
    cleanup_alert_lifecycle.append(scorable_item)
    opened = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    assert opened["alert_flagged"] is True

    result = alert_engine.resolve_by_item(
        scorable_item, scorable_item_host_serial_code
    )

    assert result["inspection"]["item_serial_code"] == scorable_item_host_serial_code


@needs_database
@needs_models
def test_resolve_by_item_host_serial_code_historis_ditolak(scorable_item):
    """Kasus 2 - host_serial_code HISTORIS (bukan cycle aktif) ditolak
    dengan HostSerialNotCurrent, BUKAN dipakai resolve cycle aktif."""
    stale_host_serial_code = f"STALE-{scorable_item}-00"

    with pytest.raises(alert_engine.HostSerialNotCurrent) as excinfo:
        alert_engine.resolve_by_item(
            scorable_item, stale_host_serial_code
        )

    assert excinfo.value.given_host_serial_code == stale_host_serial_code
    assert excinfo.value.item_id == scorable_item


@needs_database
@needs_models
def test_resolve_by_item_host_serial_code_current_tidak_pengaruhi_item_lain(
    scorable_item, scorable_item_host_serial_code, cleanup_alert_lifecycle
):
    """Kasus 4 - resolve satu item dengan host_serial_code current TIDAK
    memengaruhi item lain (item lain tidak ikut punya inspection baru)."""
    cleanup_alert_lifecycle.append(scorable_item)
    opened = _score_and_flag(scorable_item, scorable_item_host_serial_code, 0.5)
    assert opened["alert_flagged"] is True

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM predictive.inspection_history "
                "WHERE split_part(item_serial_code, '-', 2) != %s", (scorable_item,)
            )
            before = cur.fetchone()[0]

    alert_engine.resolve_by_item(
        scorable_item, scorable_item_host_serial_code
    )

    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM predictive.inspection_history "
                "WHERE split_part(item_serial_code, '-', 2) != %s", (scorable_item,)
            )
            after = cur.fetchone()[0]

    assert after == before, "resolve satu item tidak boleh membuat inspection untuk item lain"
