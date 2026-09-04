"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.intervention) -
lihat docs/DATABASE.md dan docs §10/22/23 master prompt refactor.

Tidak ada klasifikasi jenis intervention - satu POST berarti satu perbaikan
terjadi, apa pun bentuknya (keputusan user, docs/DECISIONS.md §25 update).
Tidak ada outcome/remark/external_* juga - body POST /api/v1/interventions
cuma host_serial_code (docs/DECISIONS.md §28), tidak ada idempotency key
eksternal (retry membuat baris baru, trade-off yang disetujui eksplisit).

Minor repair TIDAK menutup installation cycle - intervention_seq naik DALAM
cycle aktif yang sama (predictive/cycles.py), bukan membuka cycle baru.
"""

from __future__ import annotations

import pandas as pd

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "intervention_id", "item_id", "cycle_id", "intervention_seq", "alert_id",
    "performed_at", "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def list_for_cycle(cycle_id: str) -> list[dict]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS} FROM predictive.intervention
                WHERE cycle_id = %s ORDER BY intervention_seq
                """,
                (cycle_id,),
            )
            rows = cur.fetchall()
    return [_row_to_dict(row) for row in rows]


def record_intervention(
    item_id: str,
    performed_at: pd.Timestamp,
    alert_id: int | None = None,
) -> dict:
    """Catat satu intervention (perbaikan) untuk `item_id`, DALAM cycle
    aktifnya saat ini. Tidak idempotent - tidak ada identifier eksternal
    untuk dideteksi ulang (docs/DECISIONS.md §28), setiap panggilan selalu
    membuat baris baru."""
    cycle = cycle_store.ensure_active_cycle(item_id)
    cycle_id = cycle["cycle_id"]
    performed_at_value = (
        performed_at.to_pydatetime() if isinstance(performed_at, pd.Timestamp) else performed_at
    )

    with db.connect() as conn:
        with conn.cursor() as cur:
            # Kunci baris cycle ini supaya dua intervention untuk cycle yang
            # SAMA tidak bisa menghitung intervention_seq berikutnya secara
            # bersamaan (race condition) - writer kedua menunggu, bukan
            # gagal karena UNIQUE(cycle_id, intervention_seq).
            cur.execute(
                "SELECT cycle_id FROM predictive.item_cycle WHERE cycle_id = %s FOR UPDATE",
                (cycle_id,),
            )
            cur.execute(
                "SELECT COALESCE(MAX(intervention_seq), -1) + 1 "
                "FROM predictive.intervention WHERE cycle_id = %s",
                (cycle_id,),
            )
            next_seq = cur.fetchone()[0]

            cur.execute(
                f"""
                INSERT INTO predictive.intervention
                    (item_id, cycle_id, intervention_seq, alert_id, performed_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (cycle["item_id"], cycle_id, next_seq, alert_id, performed_at_value),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
