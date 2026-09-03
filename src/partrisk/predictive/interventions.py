"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.intervention) -
lihat docs/DATABASE.md dan docs §10/22/23 master prompt refactor.

Tidak ada klasifikasi jenis intervention - satu POST berarti satu perbaikan
terjadi, apa pun bentuknya (keputusan user, docs/DECISIONS.md §25 update).

Minor repair TIDAK menutup installation cycle - intervention_seq naik DALAM
cycle aktif yang sama (predictive/cycles.py), bukan membuka cycle baru.
"""

from __future__ import annotations

import pandas as pd

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "intervention_id", "item_id", "cycle_id", "intervention_seq", "alert_id",
    "outcome", "action_code", "remark",
    "external_system", "external_work_order_id", "external_inspection_id",
    "external_event_id", "performed_at", "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def find_by_external_event(external_system: str, external_event_id: str) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS} FROM predictive.intervention
                WHERE external_system = %s AND external_event_id = %s
                """,
                (external_system, external_event_id),
            )
            row = cur.fetchone()
    return None if row is None else _row_to_dict(row)


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
    outcome: str | None = None,
    action_code: str | None = None,
    remark: str | None = None,
    external_system: str | None = None,
    external_work_order_id: str | None = None,
    external_inspection_id: str | None = None,
    external_event_id: str | None = None,
    alert_id: int | None = None,
) -> tuple[dict, bool]:
    """Catat satu intervention (perbaikan) untuk `item_id`, DALAM cycle
    aktifnya saat ini.

    Idempotent lewat (external_system, external_event_id): retry dengan
    identifier yang sama mengembalikan baris yang SUDAH ADA, bukan baris
    baru - lihat docs §23 master prompt.

    Return (row, created) - created=False kalau ini replay idempotent.
    """
    if external_system and external_event_id:
        existing = find_by_external_event(external_system, external_event_id)
        if existing is not None:
            return existing, False

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
                    (item_id, cycle_id, intervention_seq, alert_id, outcome,
                     action_code, remark, external_system, external_work_order_id,
                     external_inspection_id, external_event_id, performed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    cycle["item_id"], cycle_id, next_seq, alert_id, outcome,
                    action_code, remark, external_system, external_work_order_id,
                    external_inspection_id, external_event_id, performed_at_value,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row), True
