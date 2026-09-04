"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.inspection)."""

from __future__ import annotations

import pandas as pd

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "inspection_id", "item_id", "cycle_id", "inspection_seq", "alert_id",
    "performed_at", "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def record_inspection(
    item_id: str,
    performed_at: pd.Timestamp,
    alert_id: int | None = None,
) -> dict:
    """Catat satu inspection untuk `item_id`, dalam cycle aktifnya saat ini."""
    cycle = cycle_store.ensure_active_cycle(item_id)
    cycle_id = cycle["cycle_id"]
    performed_at_value = (
        performed_at.to_pydatetime() if isinstance(performed_at, pd.Timestamp) else performed_at
    )

    with db.connect() as conn:
        with conn.cursor() as cur:
            cycle_store.lock_item(cur, item_id)
            cur.execute(
                "SELECT COALESCE(MAX(inspection_seq), -1) + 1 "
                "FROM predictive.inspection WHERE cycle_id = %s",
                (cycle_id,),
            )
            next_seq = cur.fetchone()[0]

            cur.execute(
                f"""
                INSERT INTO predictive.inspection
                    (item_id, cycle_id, inspection_seq, alert_id, performed_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (cycle["item_id"], cycle_id, next_seq, alert_id, performed_at_value),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
