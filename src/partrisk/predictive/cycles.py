"""Sinkronisasi predictive.item_cycle dari siklus fisik operasional
(core.data_reader.get_cycles()) - lihat docs/DATABASE.md.

Cycle di sini MENCERMINKAN data operasional, tidak pernah menciptakan
siklus yang tidak ada di sana. Disinkron on-demand per item (dipanggil
sebelum mencatat intervention/alert), bukan disinkron massal untuk
seluruh armada.
"""

from __future__ import annotations

import pandas as pd

from partrisk.core import data_reader
from partrisk.predictive import db

# RIGHT_CENSORED_AT_DATA_END = "belum ada event penutup sampai batas data
# terakhir" (bukan penutupan fisik sungguhan) - satu-satunya alasan cycle
# tetap is_active=true. Semua reason lain (FAILURE/RETURNED/DISMANTLED)
# berarti cycle itu benar-benar sudah berakhir secara fisik.
_STILL_ACTIVE_REASON = "RIGHT_CENSORED_AT_DATA_END"


class ItemNotInstalled(LookupError):
    """Item tidak ditemukan di data operasional, atau tidak sedang
    terpasang (tidak punya cycle aktif)."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(f"PART '{item_id}' tidak punya installation cycle aktif.")


def _cycle_no(cycle_id: str) -> int:
    return int(cycle_id.rsplit(":", 1)[-1])


def sync_item_cycles(item_id: str) -> pd.DataFrame:
    """Tarik seluruh riwayat cycle item ini dari data operasional dan
    upsert ke predictive.item_cycle. Idempotent - aman dipanggil berkali-kali."""
    data_end = data_reader.get_dataset_max_event_on()
    cycles = data_reader.get_cycles(item_id, data_end)
    if cycles.empty:
        return cycles

    rows = []
    for _, row in cycles.iterrows():
        is_active = row["cycle_end_reason"] == _STILL_ACTIVE_REASON
        rows.append((
            row["installation_cycle_id"],
            row["item_identifier_clean"],
            _cycle_no(row["installation_cycle_id"]),
            row["installed_on"].to_pydatetime(),
            None if is_active else row["cycle_end_on"].to_pydatetime(),
            "INSTALLED",
            None if is_active else row["cycle_end_reason"],
            is_active,
        ))

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO predictive.item_cycle
                    (cycle_id, item_id, cycle_no, started_at, ended_at,
                     start_reason, end_reason, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cycle_id) DO UPDATE SET
                    ended_at = EXCLUDED.ended_at,
                    end_reason = EXCLUDED.end_reason,
                    is_active = EXCLUDED.is_active,
                    synced_at = now()
                """,
                rows,
            )
        conn.commit()
    return cycles


def ensure_active_cycle(item_id: str) -> dict:
    """Sinkron lalu kembalikan cycle AKTIF item ini. Raise ItemNotInstalled
    kalau item tidak dikenal atau tidak sedang terpasang - intervention/alert
    tidak bisa dicatat untuk item yang tidak punya cycle aktif."""
    sync_item_cycles(item_id)
    normalized = data_reader.normalize(item_id)

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cycle_id, item_id, cycle_no, started_at, is_active
                FROM predictive.item_cycle
                WHERE item_id = %s AND is_active
                """,
                (normalized,),
            )
            row = cur.fetchone()

    if row is None:
        raise ItemNotInstalled(item_id)
    cycle_id, resolved_item_id, cycle_no, started_at, is_active = row
    return {
        "cycle_id": cycle_id,
        "item_id": resolved_item_id,
        "cycle_no": cycle_no,
        "started_at": started_at,
        "is_active": is_active,
    }
