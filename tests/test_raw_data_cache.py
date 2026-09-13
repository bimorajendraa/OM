from __future__ import annotations

import pandas as pd
import pytest

from partrisk.core import data_reader
from partrisk.predictive import db as predictive_db
from partrisk.predictive import raw_data_cache
from tests.conftest import needs_database


@pytest.fixture
def restore_raw_cache():
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            backups = {}
            for table, columns in (
                ("raw_events_cache", raw_data_cache._EVENTS_COLUMNS),
                ("raw_cycles_cache", raw_data_cache._CYCLES_COLUMNS),
                ("raw_episodes_cache", raw_data_cache._EPISODES_COLUMNS),
            ):
                cur.execute(f"SELECT {', '.join(columns)} FROM predictive.{table}")
                backups[table] = cur.fetchall()
    yield
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            for table, columns in (
                ("raw_events_cache", raw_data_cache._EVENTS_COLUMNS),
                ("raw_cycles_cache", raw_data_cache._CYCLES_COLUMNS),
                ("raw_episodes_cache", raw_data_cache._EPISODES_COLUMNS),
            ):
                cur.execute(f"TRUNCATE predictive.{table}")
                rows = backups[table]
                if rows:
                    sql = (
                        f"INSERT INTO predictive.{table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join(['%s'] * len(columns))})"
                    )
                    cur.executemany(sql, rows)
        conn.commit()


def _synthetic_events() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "journey_id": 999001, "item_identifier_clean": "TEST-ITEM-001",
            "created_on": pd.Timestamp("2020-01-01"), "wo_type_clean": "INSTALLATION",
            "status_clean": "INSTALLED", "item_type_clean": "GATE",
            "is_failure_onset": False, "place_canonical_clean": "STASIUN TEST",
            "host_serial_code_clean": "0000001-TEST-ITEM-001-00",
        },
        {
            "journey_id": 999002, "item_identifier_clean": "TEST-ITEM-001",
            "created_on": pd.Timestamp("2020-06-01"), "wo_type_clean": None,
            "status_clean": "OK", "item_type_clean": "GATE",
            "is_failure_onset": False, "place_canonical_clean": None,
            "host_serial_code_clean": "0000001-TEST-ITEM-001-00",
        },
    ])


def _synthetic_cycles() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "installation_cycle_id": "TEST-ITEM-001:1",
            "host_serial_code_clean": "0000001-TEST-ITEM-001-00",
            "item_identifier_clean": "TEST-ITEM-001",
            "installed_on": pd.Timestamp("2020-01-01"),
            "item_model_code_clean": "TESTMODEL",
            "installed_client_clean": "TEST CLIENT",
            "failure_onset_on": None,
            "cycle_end_on": pd.Timestamp("2020-12-31"),
            "cycle_end_reason": "RIGHT_CENSORED_AT_DATA_END",
            "dataset_max_event_on": pd.Timestamp("2020-12-31"),
            "is_recon_verified_negative_eligible": False,
            "is_initial_model_cohort": True,
            "last_confirmable_observation_on": pd.Timestamp("2020-12-01"),
            "previous_cycle_lifetime_mean": None,
            "has_previous_cycle": False,
        },
    ])


def _write(table_name: str, columns: tuple[str, ...], frame: pd.DataFrame) -> None:
    """`_replace_table()` sekarang butuh `cur` dari luar (1 transaksi
    gabungan untuk refresh() sungguhan) - helper ini buka koneksi sendiri
    khusus buat test yang menguji 1 tabel saja."""
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            raw_data_cache._replace_table(cur, table_name, columns, frame)
        conn.commit()


def _synthetic_episodes() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "onset_journey_id": 999003, "item_identifier_clean": "TEST-ITEM-002",
            "failure_onset_on": pd.Timestamp("2020-03-01"), "item_type_clean": "GATE",
            "item_model_code_clean": "TESTMODEL", "is_initial_model_cohort": True,
        },
    ])


@needs_database
def test_replace_table_dan_read_events_roundtrip(restore_raw_cache):
    _write("raw_events_cache", raw_data_cache._EVENTS_COLUMNS, _synthetic_events())
    result = raw_data_cache.read_events()
    assert len(result) == 2
    row = result.loc[result["journey_id"] == 999001].iloc[0]
    assert row["item_identifier_clean"] == "TEST-ITEM-001"
    assert row["status_clean"] == "INSTALLED"
    assert pd.isna(result.loc[result["journey_id"] == 999002, "place_canonical_clean"].iloc[0])


@needs_database
def test_replace_table_dan_read_cycles_roundtrip(restore_raw_cache):
    _write("raw_cycles_cache", raw_data_cache._CYCLES_COLUMNS, _synthetic_cycles())
    result = raw_data_cache.read_cycles()
    assert len(result) == 1
    row = result.iloc[0]
    assert row["installation_cycle_id"] == "TEST-ITEM-001:1"
    assert row["cycle_end_reason"] == "RIGHT_CENSORED_AT_DATA_END"
    assert bool(row["is_initial_model_cohort"]) is True
    assert pd.isna(row["previous_cycle_lifetime_mean"])


@needs_database
def test_replace_table_dan_read_episodes_roundtrip(restore_raw_cache):
    _write("raw_episodes_cache", raw_data_cache._EPISODES_COLUMNS, _synthetic_episodes())
    result = raw_data_cache.read_episodes()
    assert len(result) == 1
    assert result.iloc[0]["item_identifier_clean"] == "TEST-ITEM-002"


@needs_database
def test_replace_table_full_replace_bukan_incremental(restore_raw_cache):
    _write("raw_events_cache", raw_data_cache._EVENTS_COLUMNS, _synthetic_events())
    assert len(raw_data_cache.read_events()) == 2

    satu_baris = _synthetic_events().iloc[[0]]
    _write("raw_events_cache", raw_data_cache._EVENTS_COLUMNS, satu_baris)
    result = raw_data_cache.read_events()
    assert len(result) == 1, "baris lama harus hilang total, bukan digabung dengan yang baru"


@needs_database
def test_read_raises_saat_cache_kosong(restore_raw_cache):
    with predictive_db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE predictive.raw_events_cache")
        conn.commit()
    with pytest.raises(raw_data_cache.RawCacheEmpty):
        raw_data_cache.read_events()


@needs_database
def test_refresh_row_count_cocok_dengan_data_reader_langsung(restore_raw_cache):
    counts = raw_data_cache.refresh()

    events = data_reader.get_events()
    cycles = data_reader.get_cycles()
    episodes = data_reader.get_failure_episodes()

    assert counts["events"] == len(events)
    assert counts["cycles"] == len(cycles)
    assert counts["episodes"] == len(episodes)
    assert len(raw_data_cache.read_events()) == len(events)
    assert len(raw_data_cache.read_cycles()) == len(cycles)
    assert len(raw_data_cache.read_episodes()) == len(episodes)
