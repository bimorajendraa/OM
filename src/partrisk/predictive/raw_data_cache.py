from __future__ import annotations

import numpy as np
import pandas as pd

from partrisk.core import data_reader
from partrisk.predictive import db

_EVENTS_COLUMNS = (
    "journey_id", "item_identifier_clean", "created_on", "wo_type_clean",
    "status_clean", "item_type_clean", "is_failure_onset",
    "place_canonical_clean", "host_serial_code_clean",
)
_CYCLES_COLUMNS = (
    "installation_cycle_id", "host_serial_code_clean", "item_identifier_clean",
    "installed_on", "item_model_code_clean", "installed_client_clean",
    "failure_onset_on", "cycle_end_on", "cycle_end_reason",
    "dataset_max_event_on", "is_recon_verified_negative_eligible",
    "is_initial_model_cohort", "last_confirmable_observation_on",
    "previous_cycle_lifetime_mean", "has_previous_cycle",
)
_EPISODES_COLUMNS = (
    "onset_journey_id", "item_identifier_clean", "failure_onset_on",
    "item_type_clean", "item_model_code_clean", "is_initial_model_cohort",
)

_TIMESTAMP_COLUMNS = {
    "created_on", "installed_on", "failure_onset_on", "cycle_end_on",
    "dataset_max_event_on", "last_confirmable_observation_on",
}


class RawCacheEmpty(RuntimeError):
    """read_events()/read_cycles()/read_episodes() dipanggil sebelum
    refresh() pernah jalan sekali pun - pesan jelas, bukan DataFrame kosong
    yang diam-diam salah dipakai buat training."""


def _to_sql_value(value):
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, (np.bool_, np.integer, np.floating)):
        return None if pd.isna(value) else value.item()
    if pd.isna(value):
        return None
    return value


def _replace_table(cur, table_name: str, columns: tuple[str, ...], frame: pd.DataFrame) -> None:
    values = [
        tuple(_to_sql_value(row[c]) for c in columns)
        for _, row in frame[list(columns)].iterrows()
    ]
    sql = (
        f"INSERT INTO predictive.{table_name} ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})"
    )
    cur.execute(f"TRUNCATE predictive.{table_name}")
    if values:
        cur.executemany(sql, values)


def _read_table(table_name: str, columns: tuple[str, ...]) -> pd.DataFrame:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(columns)} FROM predictive.{table_name}")
            rows = cur.fetchall()
    if not rows:
        raise RawCacheEmpty(
            f"predictive.{table_name} masih kosong - jalankan "
            "'python -m partrisk.cli refresh-raw-data-cache' dulu."
        )
    frame = pd.DataFrame(rows, columns=columns)
    for column in columns:
        if column in _TIMESTAMP_COLUMNS:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def refresh() -> dict[str, int]:
    data_end = data_reader.get_dataset_max_event_on()
    events = data_reader.get_events(as_of=data_end)
    cycles = data_reader.get_cycles(dataset_max_event_on=data_end)
    episodes = data_reader.get_failure_episodes(as_of=data_end)
    with db.connect() as conn:
        with conn.cursor() as cur:
            _replace_table(cur, "raw_events_cache", _EVENTS_COLUMNS, events)
            _replace_table(cur, "raw_cycles_cache", _CYCLES_COLUMNS, cycles)
            _replace_table(cur, "raw_episodes_cache", _EPISODES_COLUMNS, episodes)
        conn.commit()
    return {"events": len(events), "cycles": len(cycles), "episodes": len(episodes)}


def read_events() -> pd.DataFrame:
    return _read_table("raw_events_cache", _EVENTS_COLUMNS)


def read_cycles() -> pd.DataFrame:
    return _read_table("raw_cycles_cache", _CYCLES_COLUMNS)


def read_episodes() -> pd.DataFrame:
    return _read_table("raw_episodes_cache", _EPISODES_COLUMNS)
