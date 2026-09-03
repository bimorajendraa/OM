from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from partrisk.core import config
from partrisk.engines import predict as failure_model
from tests.conftest import needs_database, needs_models


SAMPLE_SIZE = 6


@pytest.fixture(scope="module")
def sample(batch) -> pd.DataFrame:
    frame = batch.frame
    positions = np.unique(
        np.linspace(0, len(frame) - 1, SAMPLE_SIZE).astype(int)
    )
    return frame.iloc[positions]


@needs_database
@needs_models
def test_probabilitas_kerusakan_batch_sama_dengan_single(sample):
    for _, row in sample.iterrows():
        single = failure_model.predict(row["item_id"])
        for days in config.PREDICTION_HORIZON_DAYS:
            column = f"failure_probability_{days}d"
            assert single[column] == row[column], (
                f"{row['item_id']} horizon {days}d: "
                f"single={single[column]} batch={row[column]}"
            )


@needs_database
@needs_models
def test_kelompok_risiko_kerusakan_batch_sama_dengan_single(sample):
    for _, row in sample.iterrows():
        single = failure_model.predict(row["item_id"])
        assert single["risk_level"] == row["failure_risk_level"]


@needs_database
@needs_models
def test_populasi_batch_sama_dengan_yang_dipakai_menyetel_ambang(batch):
    metadata = failure_model._load_failure_model()[2]
    basis = metadata["cutoff_basis"]
    if metadata["fleet_snapshot_at"] != str(batch.data_end):
        pytest.skip("database sudah bertambah sejak model dilatih")

    assert len(batch.frame) == basis["active_parts_scored"]
    high = int(batch.frame["failure_risk_level"].eq("HIGH").sum())
    assert high == basis["flagged_high"]


@needs_database
@needs_models
def test_urutan_prioritas_konsisten_dengan_kelompok_risiko(batch):
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranks = batch.frame["failure_risk_level"].map(order).to_numpy()
    assert np.all(np.diff(ranks) >= 0)


@needs_database
@needs_models
def test_risiko_kumulatif_tidak_pernah_menurun(batch):
    horizons = config.PREDICTION_HORIZON_DAYS
    for earlier, later in zip(horizons, horizons[1:]):
        assert (
            batch.frame[f"failure_probability_{earlier}d"]
            <= batch.frame[f"failure_probability_{later}d"] + 1e-12
        ).all()
