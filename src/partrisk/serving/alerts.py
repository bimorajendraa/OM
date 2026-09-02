from __future__ import annotations

import threading
from dataclasses import dataclass

import pandas as pd


OPEN, RESOLVED = "OPEN", "RESOLVED"


@dataclass
class Alert:
    item_id: str
    opened_at: str
    score: float
    threshold: float | None
    model_version: str
    status: str = OPEN
    resolved_at: str | None = None


_alerts: dict[str, Alert] = {}
_lock = threading.Lock()


def register_flagged(
    item_ids: pd.Series, scores: pd.Series, threshold: float | None, model_version: str
) -> None:
    """Buka alert untuk PART yang lolos gerbang presisi (gate_flagged=True).

    PART yang sudah punya alert OPEN TIDAK dibuka ulang - ini yang mencegah
    alert berulang untuk PART yang sama sebelum inspeksi/maintenance
    sebelumnya selesai. opened_at/score/threshold/model_version dibekukan
    pada saat pertama kali terbuka, bukan diperbarui tiap siklus batch.
    """
    now = pd.Timestamp.now(tz="UTC").isoformat()
    with _lock:
        for item_id, score in zip(item_ids, scores):
            existing = _alerts.get(item_id)
            if existing is not None and existing.status == OPEN:
                continue
            _alerts[item_id] = Alert(
                item_id=item_id,
                opened_at=now,
                score=float(score),
                threshold=float(threshold) if threshold is not None else None,
                model_version=model_version,
            )


def annotate(frame: pd.DataFrame) -> pd.DataFrame:
    """Tempel status alert OPEN (kalau ada) ke tiap baris `frame` (kolom `item_id`)."""
    with _lock:
        rows = [
            {
                "item_id": alert.item_id,
                "alert_status": alert.status,
                "alert_opened_at": alert.opened_at,
                "alert_score_at_open": alert.score,
                "alert_threshold_at_open": alert.threshold,
                "alert_model_version": alert.model_version,
            }
            for alert in _alerts.values()
            if alert.status == OPEN
        ]
    open_frame = pd.DataFrame(rows, columns=[
        "item_id", "alert_status", "alert_opened_at",
        "alert_score_at_open", "alert_threshold_at_open", "alert_model_version",
    ])
    return frame.merge(open_frame, on="item_id", how="left")


def resolve(item_id: str) -> bool:
    """Tandai alert PART ini selesai diinspeksi/dimaintenance.

    Setelah ini, penilaian berikutnya akan membuka alert BARU untuk PART ini
    hanya kalau kondisi terkini masih memenuhi aturan risiko - bukan otomatis
    dipromosikan lagi.
    """
    with _lock:
        alert = _alerts.get(item_id)
        if alert is None or alert.status != OPEN:
            return False
        alert.status = RESOLVED
        alert.resolved_at = pd.Timestamp.now(tz="UTC").isoformat()
        return True


def open_count() -> int:
    with _lock:
        return sum(1 for alert in _alerts.values() if alert.status == OPEN)


def open_lead_times_days() -> list[float]:
    """Umur (hari) tiap alert OPEN saat ini - berapa lama sudah menunggu
    diselesaikan, dihitung dari `opened_at` sampai sekarang."""
    now = pd.Timestamp.now(tz="UTC")
    with _lock:
        opened_at = [alert.opened_at for alert in _alerts.values() if alert.status == OPEN]
    return [(now - pd.Timestamp(ts)).total_seconds() / 86400.0 for ts in opened_at]


def clear() -> None:
    """Untuk test - kosongkan seluruh state alert."""
    with _lock:
        _alerts.clear()
