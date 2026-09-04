"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.inspection)."""

from __future__ import annotations

import pandas as pd

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "inspection_id", "item_id", "host_serial_code", "inspection_seq", "alert_id",
    "external_event_id", "performed_at", "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def find_by_external_event_id(external_event_id: str) -> dict | None:
    """Idempotency lookup, dipakai `alerts.resolve_by_item()`."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM predictive.inspection "
                "WHERE external_event_id = %s",
                (external_event_id,),
            )
            row = cur.fetchone()
    return None if row is None else _row_to_dict(row)


def record_inspection(
    item_id: str,
    performed_at: pd.Timestamp,
    alert_id: int | None = None,
    external_event_id: str | None = None,
) -> dict:
    """Catat satu inspection untuk `item_id`, dalam cycle aktifnya saat ini."""
    cycle = cycle_store.ensure_active_cycle(item_id)
    host_serial_code = cycle["cycle_id"]
    performed_at_value = (
        performed_at.to_pydatetime() if isinstance(performed_at, pd.Timestamp) else performed_at
    )

    with db.connect() as conn:
        with conn.cursor() as cur:
            cycle_store.lock_item(cur, item_id)
            cur.execute(
                "SELECT COALESCE(MAX(inspection_seq), -1) + 1 "
                "FROM predictive.inspection WHERE host_serial_code = %s",
                (host_serial_code,),
            )
            next_seq = cur.fetchone()[0]

            cur.execute(
                f"""
                INSERT INTO predictive.inspection
                    (item_id, host_serial_code, inspection_seq, alert_id, external_event_id, performed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (cycle["item_id"], host_serial_code, next_seq, alert_id, external_event_id, performed_at_value),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
