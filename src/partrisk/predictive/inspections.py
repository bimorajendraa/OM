"""Pencatatan tindakan teknisi/aplikasi eksternal (predictive.inspection)."""

from __future__ import annotations

from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db

_COLUMNS = (
    "inspection_id", "item_id", "host_serial_code", "inspection_seq", "alert_id",
    "idempotency_key", "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def find_by_idempotency_key(idempotency_key: str) -> dict | None:
    """Idempotency lookup, dipakai `alerts.resolve_by_item()`."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM predictive.inspection "
                "WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            row = cur.fetchone()
    return None if row is None else _row_to_dict(row)


def record_inspection(
    item_id: str,
    alert_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Catat satu inspection untuk `item_id`, dalam cycle aktifnya saat ini."""
    cycle = cycle_store.ensure_active_cycle(item_id)
    host_serial_code = cycle["cycle_id"]

    with db.connect() as conn:
        with conn.cursor() as cur:
            cycle_store.lock_item(cur, item_id)

            if idempotency_key is not None:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM predictive.inspection "
                    "WHERE idempotency_key = %s",
                    (idempotency_key,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    # Retry idempotent tumpang-tindih dengan request yang masih
                    # in-flight: cek di luar lock (di alerts.resolve_by_item)
                    # sudah lolos untuk keduanya, jadi harus dicek ULANG di
                    # sini setelah pegang advisory lock, sebelum INSERT -
                    # kalau tidak, request kedua akan menabrak
                    # ux_inspection_idempotency_key (UniqueViolation -> 500).
                    return _row_to_dict(existing)

            cur.execute(
                "SELECT COALESCE(MAX(inspection_seq), -1) + 1 "
                "FROM predictive.inspection WHERE host_serial_code = %s",
                (host_serial_code,),
            )
            next_seq = cur.fetchone()[0]

            cur.execute(
                f"""
                INSERT INTO predictive.inspection
                    (item_id, host_serial_code, inspection_seq, alert_id, idempotency_key)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (cycle["item_id"], host_serial_code, next_seq, alert_id, idempotency_key),
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
