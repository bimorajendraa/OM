from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from partrisk.core import config
from partrisk.serving import batch as data_state
from partrisk.serving.single import RISK_LEVELS, recommend


def test_setiap_kelompok_risiko_punya_rekomendasi():
    for failure_level in RISK_LEVELS:
        decision = recommend(failure_level)
        assert decision["priority"] in RISK_LEVELS
        assert decision["action"]
        assert decision["message"]
        assert decision["based_on"] == {"failure_risk_level": failure_level}


def test_kelompok_risiko_tidak_dikenal_ditolak():
    with pytest.raises(ValueError):
        recommend("SANGAT_TINGGI")


def _terminal_raw(rows: list[dict]) -> pd.DataFrame:
    base = {
        "terminal_serial_code_clean": None,
        "terminal_model_name_clean": None,
        "terminal_inventory_item_id": None,
        "parent_link_quality_status": "VALID_POINT_IN_TIME_RELATION",
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def _scored_frame(rows: list[dict]) -> pd.DataFrame:
    base = {
        "failure_risk_level": "LOW", "tier_score": 0.1,
        f"failure_probability_{config.TARGET_HORIZON_DAYS}d": 0.1,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_attach_terminal_hanya_memakai_status_relasi_yang_bisa_dipercaya():
    frame = _scored_frame([{"item_id": "A"}, {"item_id": "B"}])
    terminal_raw = _terminal_raw([
        {
            "item_identifier_clean": "A", "terminal_inventory_item_id": 1,
            "parent_link_quality_status": "VALID_POINT_IN_TIME_RELATION",
        },
        {
            "item_identifier_clean": "B", "terminal_inventory_item_id": 2,
            "parent_link_quality_status": "PARENT_NOT_TERMINAL",
        },
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.set_index("item_id").loc["A", "terminal_id"] == "1"
    assert pd.isna(result.set_index("item_id").loc["B", "terminal_id"])


def test_attach_terminal_ambil_relasi_terbaru_per_part():
    frame = _scored_frame([{"item_id": "A"}])
    terminal_raw = _terminal_raw([
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 1},
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 2},
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.loc[0, "terminal_id"] == "2"


def test_attach_terminal_id_berbentuk_string_bersih_tanpa_desimal():
    frame = _scored_frame([{"item_id": "A"}])
    terminal_raw = _terminal_raw([
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 12345},
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.loc[0, "terminal_id"] == "12345"


def test_build_work_queue_confirmed_selalu_masuk_terlepas_dari_kapasitas():
    # CONFIRMED tidak boleh dipotong kapasitas - docs/DECISIONS.md §45.
    gate_flagged = np.array([True, True, True, True, True])
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    tier = data_state.build_work_queue(gate_flagged, scores, capacity=3)
    assert list(tier) == ["CONFIRMED"] * 5


def test_build_work_queue_ranked_mengisi_sisa_kapasitas_dari_skor_tertinggi():
    gate_flagged = np.array([True, False, False, False, False])
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    tier = data_state.build_work_queue(gate_flagged, scores, capacity=3)
    # sisa kapasitas diisi RANKED dari skor tertinggi non-gate_flagged.
    assert list(tier) == ["CONFIRMED", "RANKED", "RANKED", None, None]


def test_build_work_queue_tidak_ada_ranked_kalau_confirmed_sudah_penuhi_kapasitas():
    gate_flagged = np.array([True, True, True, False, False])
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    tier = data_state.build_work_queue(gate_flagged, scores, capacity=2)
    assert list(tier) == ["CONFIRMED", "CONFIRMED", "CONFIRMED", None, None]


def test_build_work_queue_kapasitas_nol_tidak_menghasilkan_ranked():
    gate_flagged = np.array([False, False, False])
    scores = np.array([0.9, 0.8, 0.7])
    tier = data_state.build_work_queue(gate_flagged, scores, capacity=0)
    assert list(tier) == [None, None, None]


def test_build_work_queue_tanpa_yang_confirmed_sama_sekali():
    gate_flagged = np.array([False, False, False, False])
    scores = np.array([0.4, 0.9, 0.2, 0.7])
    tier = data_state.build_work_queue(gate_flagged, scores, capacity=2)
    # RANKED dari skor tertinggi, bukan urutan baris.
    assert list(tier) == [None, "RANKED", None, "RANKED"]
