"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.inspection_history)."""

from __future__ import annotations

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "inspection_id", "item_serial_code", "inspection_seq", "alert_id",
    "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def record_inspection(
    item_id: str,
    alert_id: int | None = None,
) -> dict:
    """Catat satu inspection untuk `item_id`, dalam cycle aktifnya saat ini."""
    cycle = cycle_store.ensure_active_cycle(item_id)
    item_serial_code = cycle["cycle_id"]

    with db.connect() as conn:
        with conn.cursor() as cur:
            cycle_store.lock_item(cur, item_id)

            cur.execute(
                "SELECT COALESCE(MAX(inspection_seq), -1) + 1 "
                "FROM predictive.inspection_history WHERE item_serial_code = %s",
                (item_serial_code,),
            )
            next_seq = cur.fetchone()[0]

            cur.execute(
                f"""
                INSERT INTO predictive.inspection_history
                    (item_serial_code, inspection_seq, alert_id)
                VALUES (%s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (item_serial_code, next_seq, alert_id),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
