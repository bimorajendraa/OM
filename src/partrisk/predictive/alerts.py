from __future__ import annotations

import logging

import pandas as pd

from partrisk.core import config
from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db
from partrisk.predictive import inspections

logger = logging.getLogger(__name__)

_ALERT_COLUMNS = (
    "prediction_id", "terminal_serial_code", "item_serial_code", "p30", "scored_at",
)
_ALERT_SELECT_COLUMNS = ", ".join(_ALERT_COLUMNS)


def _pairing_code(item_serial_code: str) -> str:
    """Identitas PART yang stabil lintas siklus perbaikan - bagian tengah
    `item_serial_code` (format MODEL-PAIRINGCODE-REPAIRSEQ)."""
    return item_serial_code.split("-")[1]


class AlertNotFound(LookupError):
    def __init__(self, prediction_id: int) -> None:
        self.prediction_id = prediction_id
        super().__init__(f"Alert (prediction_id={prediction_id}) tidak ditemukan.")


class AlertNotOpen(ValueError):
    def __init__(self, prediction_id: int, status: str) -> None:
        self.prediction_id = prediction_id
        self.status = status
        super().__init__(f"Alert (prediction_id={prediction_id}) berstatus {status}, bukan OPEN.")


class HostSerialNotCurrent(ValueError):

    def __init__(self, item_id: str, given: str, current: str) -> None:
        self.item_id = item_id
        self.given_host_serial_code = given
        self.current_host_serial_code = current
        super().__init__(
            f"host_serial_code {given!r} bukan cycle aktif item {item_id!r} saat ini "
            f"(cycle aktif: {current!r}) - kemungkinan serial code lama/sudah diganti."
        )


class NoOpenAlert(LookupError):
    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(f"Item {item_id!r} tidak sedang punya alert OPEN.")


class AlertCycleMismatch(ValueError):
    def __init__(self, prediction_id: int, alert_host_serial_code: str, current_host_serial_code: str) -> None:
        self.prediction_id = prediction_id
        self.alert_host_serial_code = alert_host_serial_code
        self.current_host_serial_code = current_host_serial_code
        super().__init__(
            f"Alert (prediction_id={prediction_id}) dibuka untuk cycle {alert_host_serial_code!r}, "
            f"tapi cycle aktif item sekarang {current_host_serial_code!r} - kemungkinan item "
            "sudah dilepas/dipasang ulang."
        )


def _row_to_alert(row) -> dict:
    return dict(zip(_ALERT_COLUMNS, row))


def _run_succeeded(table_ref: str = "item_prediction") -> str:
    return (
        f"EXISTS (SELECT 1 FROM predictive.model_run mr "
        f"WHERE mr.run_id = {table_ref}.run_id AND mr.status = 'SUCCEEDED')"
    )


def get_alert(prediction_id: int) -> dict | None:
   
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALERT_SELECT_COLUMNS} FROM predictive.item_prediction "
                f"WHERE prediction_id = %s AND alert_flagged AND {_run_succeeded()}",
                (prediction_id,),
            )
            row = cur.fetchone()
    return None if row is None else _row_to_alert(row)


def open_alerts_by_item(item_ids: list[str] | None = None) -> dict[str, dict]:
    
    query = f"""
        SELECT DISTINCT ON (item_serial_code) {_ALERT_SELECT_COLUMNS}
        FROM predictive.item_prediction
        WHERE alert_flagged
          AND {_run_succeeded()}
          AND NOT EXISTS (
              SELECT 1 FROM predictive.inspection_history
              WHERE inspection_history.prediction_id = item_prediction.prediction_id
          )
    """
    params: tuple = ()
    if item_ids is not None:
        query += " AND split_part(item_serial_code, '-', 2) = ANY(%s)"
        params = (list(item_ids),)
    query += " ORDER BY item_serial_code, scored_at DESC"

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    alerts = [_row_to_alert(row) for row in rows]
    by_item: dict[str, dict] = {}
    for alert in alerts:
        item_id = _pairing_code(alert["item_serial_code"])
        existing = by_item.get(item_id)
        if existing is not None:
            logger.error(
                "DUPLICATE OPEN alert untuk item_id=%s: prediction_id %s dan %s "
                "sama-sama belum di-inspect (biasanya cycle lama belum sempat "
                "di-auto-resolve) - pakai yang scored_at terbaru, sisanya butuh "
                "investigasi manual.",
                item_id, existing["prediction_id"], alert["prediction_id"],
            )
            if alert["scored_at"] > existing["scored_at"]:
                by_item[item_id] = alert
        else:
            by_item[item_id] = alert
    return by_item


def _next_inspection_seq(cur, item_serial_code: str) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(inspection_seq), -1) + 1 FROM predictive.inspection_history WHERE item_serial_code = %s",
        (item_serial_code,),
    )
    return cur.fetchone()[0]


def _last_closure(cur, item_serial_code: str) -> tuple[pd.Timestamp, float] | None:
    cur.execute(
        """
        SELECT ih.created_at, ip.p30
        FROM predictive.inspection_history ih
        JOIN predictive.item_prediction ip ON ip.prediction_id = ih.prediction_id
        WHERE ih.item_serial_code = %s
        ORDER BY ih.created_at DESC LIMIT 1
        """,
        (item_serial_code,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    closed_at, previous_score = row
    return pd.Timestamp(closed_at), previous_score


def _emergency_override(current_score: float, previous_score: float | None) -> bool:
    if current_score >= config.ALERT_EMERGENCY_SCORE_ABSOLUTE:
        return True
    if previous_score is not None and (current_score - previous_score) >= config.ALERT_EMERGENCY_SCORE_JUMP:
        return True
    return False


def _is_suppressed(cur, item_serial_code: str, current_score: float, scored_at: pd.Timestamp) -> bool:
    closure = _last_closure(cur, item_serial_code)
    if closure is None:
        return False
    closed_at, previous_score = closure
    suppression_until = closed_at + pd.Timedelta(days=config.ALERT_SUPPRESSION_DAYS)
    if pd.Timestamp(scored_at) >= suppression_until:
        return False
    return not _emergency_override(current_score, previous_score)


def _has_open_alert(cur, item_serial_code: str) -> bool:
    cur.execute(
        f"""
        SELECT 1 FROM predictive.item_prediction ip
        WHERE ip.item_serial_code = %s AND ip.alert_flagged
          AND {_run_succeeded("ip")}
          AND NOT EXISTS (
              SELECT 1 FROM predictive.inspection_history ih
              WHERE ih.prediction_id = ip.prediction_id
          )
        LIMIT 1
        """,
        (item_serial_code,),
    )
    return cur.fetchone() is not None


def compute_alert_flagged(cur, frame: pd.DataFrame, scored_at: pd.Timestamp) -> pd.Series:

    flagged = []
    for _, row in frame.iterrows():
        if not bool(row["gate_flagged"]):
            flagged.append(False)
            continue
        item_serial_code = str(row["host_serial_code"])
        item_id = _pairing_code(item_serial_code)
        cycle_store.lock_item(cur, item_id)
        score = float(row["failure_probability_30d"])
        if _has_open_alert(cur, item_serial_code):
            flagged.append(False)
            continue
        flagged.append(not _is_suppressed(cur, item_serial_code, score, scored_at))
    return pd.Series(flagged, index=frame.index)


def _auto_resolve_if_cycle_closed(cur, alert: dict) -> dict | None:
    item_id = _pairing_code(alert["item_serial_code"])
    status = cycle_store.cycle_status(item_id, alert["item_serial_code"])
    if status is None or status["is_active"]:
        return None

    cycle_store.lock_item(cur, item_id)
    next_seq = _next_inspection_seq(cur, alert["item_serial_code"])
    cur.execute(
        f"""
        INSERT INTO predictive.inspection_history
            (item_serial_code, inspection_seq, prediction_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (prediction_id) DO NOTHING
        RETURNING {inspections._SELECT_COLUMNS}
        """,
        (alert["item_serial_code"], next_seq, alert["prediction_id"]),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return inspections._row_to_dict(row)


def auto_resolve_closed_cycles(item_ids: list[str] | None = None) -> list[int]:
    open_alerts = open_alerts_by_item(item_ids)
    resolved_ids: list[int] = []
    for alert in open_alerts.values():
        with db.connect() as conn:
            with conn.cursor() as cur:
                resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if resolved is not None:
            resolved_ids.append(alert["prediction_id"])
    return resolved_ids


def resolve_by_item(item_id: str, host_serial_code: str) -> dict:
    current_cycle = cycle_store.ensure_active_cycle(item_id)
    if current_cycle["cycle_id"] != host_serial_code:
        raise HostSerialNotCurrent(item_id, host_serial_code, current_cycle["cycle_id"])

    alert = open_alerts_by_item([item_id]).get(item_id)
    if alert is None:
        raise NoOpenAlert(item_id)

    result = resolve_with_inspection(alert["prediction_id"])
    return {"inspection": result["inspection"], "alert": result["alert"]}


def resolve_with_inspection(prediction_id: int) -> dict:
    alert = get_alert(prediction_id)
    if alert is None:
        raise AlertNotFound(prediction_id)

    item_id = _pairing_code(alert["item_serial_code"])
    current_cycle = cycle_store.ensure_active_cycle(item_id)
    if current_cycle["cycle_id"] != alert["item_serial_code"]:
        with db.connect() as conn:
            with conn.cursor() as cur:
                auto_resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if auto_resolved is not None:
            raise AlertNotOpen(prediction_id, "RESOLVED")
        raise AlertCycleMismatch(prediction_id, alert["item_serial_code"], current_cycle["cycle_id"])

    with db.connect() as conn:
        with conn.cursor() as cur:
            cycle_store.lock_item(cur, item_id)

            cur.execute(
                "SELECT 1 FROM predictive.inspection_history WHERE prediction_id = %s",
                (prediction_id,),
            )
            if cur.fetchone() is not None:
                raise AlertNotOpen(prediction_id, "RESOLVED")

            next_seq = _next_inspection_seq(cur, alert["item_serial_code"])

            cur.execute(
                f"""
                INSERT INTO predictive.inspection_history
                    (item_serial_code, inspection_seq, prediction_id)
                VALUES (%s, %s, %s)
                RETURNING {inspections._SELECT_COLUMNS}
                """,
                (alert["item_serial_code"], next_seq, prediction_id),
            )
            inspection_row = inspections._row_to_dict(cur.fetchone())
        conn.commit()

    return {"inspection": inspection_row, "alert": alert}
