"""Alert lifecycle persisten (predictive.alert) - menggantikan serving/alerts.py
in-memory.
"""

from __future__ import annotations

import logging

import pandas as pd

from partrisk.core import config
from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db
from partrisk.predictive import inspections

logger = logging.getLogger(__name__)

_ALERT_COLUMNS = (
    "alert_id", "terminal_serial_code", "item_id", "host_serial_code", "inspection_seq",
    "prediction_id", "opened_at", "opened_score", "status",
    "resolved_at", "suppression_until", "created_at", "updated_at",
)
_ALERT_SELECT_COLUMNS = ", ".join(_ALERT_COLUMNS)


class AlertNotFound(LookupError):
    def __init__(self, alert_id: int) -> None:
        self.alert_id = alert_id
        super().__init__(f"Alert {alert_id} tidak ditemukan.")


class AlertNotOpen(ValueError):
    def __init__(self, alert_id: int, status: str) -> None:
        self.alert_id = alert_id
        self.status = status
        super().__init__(f"Alert {alert_id} berstatus {status}, bukan OPEN.")


class HostSerialNotCurrent(ValueError):
    """host_serial_code yang dikirim caller bukan cycle aktif item ini
    sekarang - kemungkinan caller pakai serial code lama (dari sebelum
    perbaikan/pemasangan ulang terakhir). docs/DECISIONS.md §41."""

    def __init__(self, item_id: str, given: str, current: str) -> None:
        self.item_id = item_id
        self.given_host_serial_code = given
        self.current_host_serial_code = current
        super().__init__(
            f"host_serial_code {given!r} bukan cycle aktif item {item_id!r} saat ini "
            f"(cycle aktif: {current!r}) - kemungkinan serial code lama/sudah diganti."
        )


class NoOpenAlert(LookupError):
    """Item tidak sedang punya alert OPEN - `POST /api/v1/inspections`
    HANYA boleh dipakai untuk merespons alert yang sudah dibuka model,
    bukan untuk mencatat perbaikan di luar itu (keputusan user)."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(f"Item {item_id!r} tidak sedang punya alert OPEN.")


class AlertCycleMismatch(ValueError):
    """Item sudah pindah cycle sejak alert ini dibuka (cycle diidentifikasi
    lewat host_serial_code - docs/DECISIONS.md §38)."""

    def __init__(self, alert_id: int, alert_host_serial_code: str, current_host_serial_code: str) -> None:
        self.alert_id = alert_id
        self.alert_host_serial_code = alert_host_serial_code
        self.current_host_serial_code = current_host_serial_code
        super().__init__(
            f"Alert {alert_id} dibuka untuk cycle {alert_host_serial_code!r}, tapi cycle aktif "
            f"item sekarang {current_host_serial_code!r} - kemungkinan item sudah dilepas/dipasang ulang."
        )


def _row_to_alert(row) -> dict:
    return dict(zip(_ALERT_COLUMNS, row))


def get_alert(alert_id: int) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALERT_SELECT_COLUMNS} FROM predictive.alert WHERE alert_id = %s",
                (alert_id,),
            )
            row = cur.fetchone()
    return None if row is None else _row_to_alert(row)


def open_alerts_by_item(item_ids: list[str] | None = None) -> dict[str, dict]:
    """Baca status alert OPEN saat ini, per item_id. Murni baca."""
    query = f"SELECT {_ALERT_SELECT_COLUMNS} FROM predictive.alert WHERE status = 'OPEN'"
    params: tuple = ()
    if item_ids is not None:
        query += " AND item_id = ANY(%s)"
        params = (list(item_ids),)

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    alerts = [_row_to_alert(row) for row in rows]
    by_item: dict[str, dict] = {}
    for alert in alerts:
        existing = by_item.get(alert["item_id"])
        if existing is not None:
            logger.error(
                "DUPLICATE OPEN alert untuk item_id=%s: alert_id %s dan %s sama-sama OPEN "
                "(seharusnya dicegah constraint ux_alert_one_open_per_item) - pakai yang "
                "opened_at terbaru, alert lain butuh investigasi manual.",
                alert["item_id"], existing["alert_id"], alert["alert_id"],
            )
            if alert["opened_at"] > existing["opened_at"]:
                by_item[alert["item_id"]] = alert
        else:
            by_item[alert["item_id"]] = alert
    return by_item


def _next_inspection_seq(cur, host_serial_code: str) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(inspection_seq), -1) + 1 FROM predictive.inspection WHERE host_serial_code = %s",
        (host_serial_code,),
    )
    return cur.fetchone()[0]


def _active_suppression(cur, item_id: str, host_serial_code: str) -> tuple[pd.Timestamp, float] | None:
    """Baris alert terbaru (kalau ada) untuk item+cycle ini yang masih dalam
    masa suppression."""
    cur.execute(
        """
        SELECT suppression_until, opened_score FROM predictive.alert
        WHERE item_id = %s AND host_serial_code = %s
          AND status = 'RESOLVED' AND suppression_until IS NOT NULL
        ORDER BY resolved_at DESC LIMIT 1
        """,
        (item_id, host_serial_code),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    suppression_until, previous_score = row
    if pd.Timestamp(suppression_until) <= pd.Timestamp.now(tz="UTC"):
        return None
    return suppression_until, previous_score


def _auto_resolve_if_cycle_closed(cur, alert: dict) -> dict | None:
    """Return baris alert yang baru di-RESOLVE (kalau cycle-nya memang sudah
    tertutup), None kalau cycle masih aktif."""
    status = cycle_store.cycle_status(alert["item_id"], alert["host_serial_code"])
    if status is None or status["is_active"]:
        return None

    cur.execute(
        f"""
        UPDATE predictive.alert
        SET status = 'RESOLVED', resolved_at = now(), updated_at = now()
        WHERE alert_id = %s AND status = 'OPEN'
        RETURNING {_ALERT_SELECT_COLUMNS}
        """,
        (alert["alert_id"],),
    )
    updated = cur.fetchone()
    if updated is None:
        return None
    return _row_to_alert(updated)


def auto_resolve_closed_cycles(item_ids: list[str] | None = None) -> list[int]:
    """RESOLVE otomatis setiap alert OPEN yang cycle-nya sudah tertutup di
    data operasional."""
    open_alerts = open_alerts_by_item(item_ids)
    resolved_ids: list[int] = []
    for alert in open_alerts.values():
        with db.connect() as conn:
            with conn.cursor() as cur:
                resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if resolved is not None:
            resolved_ids.append(alert["alert_id"])
    return resolved_ids


def _emergency_override(current_score: float, previous_score: float | None) -> bool:
    if current_score >= config.ALERT_EMERGENCY_SCORE_ABSOLUTE:
        return True
    if previous_score is not None and (current_score - previous_score) >= config.ALERT_EMERGENCY_SCORE_JUMP:
        return True
    return False


def resolve_by_item(
    item_id: str,
    host_serial_code: str,
) -> dict:
    """Jalur MANUAL diidentifikasi lewat item (bukan alert_id). Item WAJIB
    sedang punya alert OPEN - raise `NoOpenAlert` kalau tidak (keputusan
    user: endpoint ini cuma untuk merespons alert, bukan mencatat perbaikan
    di luar itu). `host_serial_code` harus cycle AKTIF item ini sekarang -
    raise `HostSerialNotCurrent` kalau caller pakai serial code lama
    (docs/DECISIONS.md §41)."""
    current_cycle = cycle_store.ensure_active_cycle(item_id)
    if current_cycle["cycle_id"] != host_serial_code:
        raise HostSerialNotCurrent(item_id, host_serial_code, current_cycle["cycle_id"])

    alert = open_alerts_by_item([item_id]).get(item_id)
    if alert is None:
        raise NoOpenAlert(item_id)

    result = resolve_with_inspection(alert["alert_id"])
    return {"inspection": result["inspection"], "alert": result["alert"]}


def evaluate_and_open(frame: pd.DataFrame, scored_at: pd.Timestamp) -> list[int]:
    """Satu siklus evaluasi alert - dipanggil sekali per scheduled scoring run.
    Hanya tier CONFIRMED yang boleh membuka alert (docs/DECISIONS.md §45).

    Return daftar alert_id yang baru dibuka pada run ini (tidak termasuk
    yang auto-resolved)."""
    auto_resolve_closed_cycles()

    flagged = frame.loc[frame["work_queue_tier"] == "CONFIRMED"]
    opened_ids: list[int] = []

    with db.connect() as conn:
        for _, row in flagged.iterrows():
            item_id = row["item_id"]
            score = float(row["failure_probability_30d"])

            try:
                cycle = cycle_store.ensure_active_cycle(item_id)
            except (cycle_store.ItemNotInstalled, cycle_store.CycleMissingHostSerialCode):
                continue
            host_serial_code = cycle["cycle_id"]

            terminal_serial_code = row.get("terminal_label")
            terminal_serial_code = None if pd.isna(terminal_serial_code) else str(terminal_serial_code)
            prediction_id = row.get("prediction_id")
            prediction_id = None if pd.isna(prediction_id) else int(prediction_id)

            with conn.cursor() as cur:
                cycle_store.lock_item(cur, item_id)
                next_seq = _next_inspection_seq(cur, host_serial_code)

                cur.execute(
                    "SELECT 1 FROM predictive.alert WHERE item_id = %s AND status = 'OPEN'",
                    (item_id,),
                )
                if cur.fetchone() is not None:
                    conn.commit()
                    continue

                suppression = _active_suppression(cur, item_id, host_serial_code)
                if suppression is not None:
                    _, previous_score = suppression
                    if not _emergency_override(score, previous_score):
                        conn.commit()
                        continue

                cur.execute(
                    """
                    INSERT INTO predictive.alert
                        (terminal_serial_code, item_id, host_serial_code,
                         inspection_seq, prediction_id, opened_at, opened_score, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN')
                    RETURNING alert_id
                    """,
                    (
                        terminal_serial_code, item_id, host_serial_code,
                        next_seq, prediction_id, scored_at.to_pydatetime(), score,
                    ),
                )
                alert_id = cur.fetchone()[0]
            conn.commit()

            opened_ids.append(alert_id)

    return opened_ids


def resolve_with_inspection(alert_id: int) -> dict:
    """Jalur MANUAL untuk mematikan alert."""
    alert = get_alert(alert_id)
    if alert is None:
        raise AlertNotFound(alert_id)
    if alert["status"] != "OPEN":
        raise AlertNotOpen(alert_id, alert["status"])

    current_cycle = cycle_store.ensure_active_cycle(alert["item_id"])
    if current_cycle["cycle_id"] != alert["host_serial_code"]:
        with db.connect() as conn:
            with conn.cursor() as cur:
                auto_resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if auto_resolved is not None:
            raise AlertNotOpen(alert_id, auto_resolved["status"])
        raise AlertCycleMismatch(alert_id, alert["host_serial_code"], current_cycle["cycle_id"])

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM predictive.alert WHERE alert_id = %s FOR UPDATE", (alert_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise AlertNotFound(alert_id)
            if row[0] != "OPEN":
                raise AlertNotOpen(alert_id, row[0])

            cycle_store.lock_item(cur, alert["item_id"])
            next_seq = _next_inspection_seq(cur, alert["host_serial_code"])

            cur.execute(
                f"""
                INSERT INTO predictive.inspection
                    (item_id, host_serial_code, inspection_seq, alert_id)
                VALUES (%s, %s, %s, %s)
                RETURNING {inspections._SELECT_COLUMNS}
                """,
                (alert["item_id"], alert["host_serial_code"], next_seq, alert_id),
            )
            inspection_row = inspections._row_to_dict(cur.fetchone())

            cur.execute(
                f"""
                UPDATE predictive.alert
                SET status = 'RESOLVED', resolved_at = now(),
                    suppression_until = now() + make_interval(days => %s), updated_at = now()
                WHERE alert_id = %s
                RETURNING {_ALERT_SELECT_COLUMNS}
                """,
                (config.ALERT_SUPPRESSION_DAYS, alert_id),
            )
            alert_row = _row_to_alert(cur.fetchone())
        conn.commit()

    return {"inspection": inspection_row, "alert": alert_row}
