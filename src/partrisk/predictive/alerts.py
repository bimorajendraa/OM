"""Alert lifecycle persisten (predictive.alert) - menggantikan serving/alerts.py
in-memory. Lihat docs/DATABASE.md dan docs §16-25 master prompt refactor.

Pemisahan tanggung jawab (docs §2 master prompt):
- FAILURE MODEL memutuskan skor (serving/batch.py, tidak berubah).
- ALERT ENGINE (modul ini) memutuskan apakah skor itu perlu jadi alert.
- TEKNISI/aplikasi eksternal mencatat tindakan (predictive/interventions.py).

Alert HANYA dibuka dari siklus scheduled scoring (`evaluate_and_open`,
dipanggil dari predictive/scoring.py::run_and_persist()) - TIDAK PERNAH dari
jalur baca live (serving/batch.py hanya membaca status alert yang sudah
ada, lihat open_alerts_by_item()).

DUA jalan untuk mematikan alert (klarifikasi user 2026-09-03):
1. OTOMATIS (`auto_resolve_closed_cycles`) - work order corrective/
   preventive yang berakhir dismantle SUDAH tercatat di data operasional
   (`core.data_reader.get_cycles()`) - itu sendiri sudah bukti PART
   ditangani, alert mati sendiri tanpa laporan terpisah.
2. MANUAL lewat intervention (`resolve_by_item` -> `resolve_with_intervention`,
   endpoint `POST /api/v1/interventions`, body cuma `host_serial_code`) -
   untuk perbaikan KECIL yang TIDAK PERNAH masuk data operasional (mis. cuma
   kencangkan baut) - satu-satunya cara sistem tahu itu terjadi adalah
   laporan eksplisit ini. Diidentifikasi lewat item (docs/DECISIONS.md §28),
   BUKAN alert_id - aplikasi eksternal tidak pernah tahu alert_id internal
   (tidak ada GET /alerts, lihat §26).
"""

from __future__ import annotations

import pandas as pd

from partrisk.core import config
from partrisk.predictive import cycles as cycle_store
from partrisk.predictive import db
from partrisk.predictive import interventions

_ALERT_COLUMNS = (
    "alert_id", "terminal_id", "part_type", "item_id", "cycle_id", "intervention_seq",
    "prediction_id", "opened_at", "opened_score", "status", "acknowledged_at",
    "resolved_at", "resolution_reason", "suppression_until", "created_at", "updated_at",
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


class AlertCycleMismatch(ValueError):
    """Item sudah pindah cycle sejak alert ini dibuka, TAPI cycle lamanya
    TERNYATA belum tercatat tertutup di predictive.item_cycle - keadaan
    yang seharusnya tidak terjadi (auto-resolve harusnya sudah menangani
    cycle yang benar-benar tertutup, lihat _auto_resolve_if_cycle_closed).
    Ditolak eksplisit alih-alih menempelkan intervention ke cycle yang
    sudah tidak aktif."""

    def __init__(self, alert_id: int, alert_cycle_id: str, current_cycle_id: str) -> None:
        self.alert_id = alert_id
        self.alert_cycle_id = alert_cycle_id
        self.current_cycle_id = current_cycle_id
        super().__init__(
            f"Alert {alert_id} dibuka untuk cycle {alert_cycle_id!r}, tapi cycle aktif "
            f"item sekarang {current_cycle_id!r} - kemungkinan item sudah dilepas/dipasang ulang."
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
    """Baca status alert OPEN saat ini, per item_id - dipakai serving/batch.py
    untuk menandai `alert_status`/`in_official_queue` di jalur baca live.
    TIDAK PERNAH membuka/menutup alert apa pun, murni baca."""
    query = f"SELECT {_ALERT_SELECT_COLUMNS} FROM predictive.alert WHERE status = 'OPEN'"
    params: tuple = ()
    if item_ids is not None:
        query += " AND item_id = ANY(%s)"
        params = (list(item_ids),)

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return {row[3]: _row_to_alert(row) for row in rows}  # index 3 = item_id


def _next_intervention_seq(cur, cycle_id: str) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(intervention_seq), -1) + 1 FROM predictive.intervention WHERE cycle_id = %s",
        (cycle_id,),
    )
    return cur.fetchone()[0]


def _active_suppression(cur, item_id: str, cycle_id: str) -> tuple[pd.Timestamp, float] | None:
    """Baris alert TERBARU (kalau ada) untuk item+cycle ini yang masih dalam
    masa suppression - dipakai memutuskan apakah alert baru harus ditahan."""
    cur.execute(
        """
        SELECT suppression_until, opened_score FROM predictive.alert
        WHERE item_id = %s AND cycle_id = %s
          AND status = 'RESOLVED' AND suppression_until IS NOT NULL
        ORDER BY resolved_at DESC LIMIT 1
        """,
        (item_id, cycle_id),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    suppression_until, previous_score = row
    if pd.Timestamp(suppression_until) <= pd.Timestamp.now(tz="UTC"):
        return None
    return suppression_until, previous_score


def _auto_resolve_if_cycle_closed(cur, alert: dict) -> dict | None:
    """Dua jalan untuk mematikan alert (docs - klarifikasi user 2026-09-03):
    (1) intervention tercatat lewat API (resolve_with_intervention) - untuk
        perbaikan KECIL yang tidak pernah masuk data operasional (mis.
        cuma kencangkan baut), atau
    (2) OTOMATIS di sini - work order corrective/preventive yang berakhir
        dismantle SUDAH tercatat di data operasional (data_reader.get_cycles()
        -> predictive.item_cycle, cycle_end_reason FAILURE/RETURNED/
        DISMANTLED) - itu sendiri sudah bukti PART ditangani, tidak perlu
        laporan intervention terpisah lewat API.

    Return baris alert yang baru di-RESOLVE (kalau cycle-nya memang sudah
    tertutup), None kalau cycle masih aktif (tidak melakukan apa-apa).
    """
    cur.execute(
        "SELECT is_active, end_reason FROM predictive.item_cycle WHERE cycle_id = %s",
        (alert["cycle_id"],),
    )
    row = cur.fetchone()
    if row is None or row[0]:
        return None
    _, end_reason = row

    cur.execute(
        f"""
        UPDATE predictive.alert
        SET status = 'RESOLVED', resolved_at = now(),
            resolution_reason = %s, updated_at = now()
        WHERE alert_id = %s AND status = 'OPEN'
        RETURNING {_ALERT_SELECT_COLUMNS}
        """,
        (f"OPERATIONAL_CYCLE_CLOSED:{end_reason}", alert["alert_id"]),
    )
    updated = cur.fetchone()
    if updated is None:
        return None
    return _row_to_alert(updated)


def auto_resolve_closed_cycles(item_ids: list[str] | None = None) -> list[int]:
    """Sinkron cycle lalu RESOLVE otomatis setiap alert OPEN yang cycle-nya
    ternyata sudah tertutup di data operasional (dismantle/failure/return
    sungguhan sudah tercatat) - dipanggil di awal setiap evaluate_and_open().

    Dipisah jadi fungsi sendiri (bukan inline di evaluate_and_open) supaya
    bisa juga dipanggil untuk SATU alert saja dari resolve_with_intervention
    saat mendeteksi cycle sudah berpindah.
    """
    open_alerts = open_alerts_by_item(item_ids)
    resolved_ids: list[int] = []
    for item_id, alert in open_alerts.items():
        cycle_store.sync_item_cycles(item_id)
        with db.connect() as conn:
            with conn.cursor() as cur:
                resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if resolved is not None:
            resolved_ids.append(alert["alert_id"])
    return resolved_ids


def _emergency_override(current_score: float, previous_score: float | None) -> bool:
    """docs §25 master prompt - lonjakan skor tajam atau skor sudah sangat
    tinggi membuka alert BARU walau masih dalam masa suppression. Nilai
    ambang: lihat WHY di core/config.py (belum divalidasi data nyata)."""
    if current_score >= config.ALERT_EMERGENCY_SCORE_ABSOLUTE:
        return True
    if previous_score is not None and (current_score - previous_score) >= config.ALERT_EMERGENCY_SCORE_JUMP:
        return True
    return False


def resolve_by_item(item_id: str, performed_at: pd.Timestamp) -> dict:
    """Jalur MANUAL diidentifikasi lewat item (bukan alert_id) - dipakai
    endpoint `POST /api/v1/interventions`, body-nya cuma `host_serial_code`
    (diresolve ke `item_id` internal oleh caller lewat
    `core.data_reader.resolve_item_by_host_serial_code()` sebelum masuk sini
    - lihat docs/DECISIONS.md §28).

    Kalau item ini SEDANG punya alert OPEN, resolve alert itu - delegasi
    penuh ke `resolve_with_intervention()` (transaksi/suppression/cycle-
    mismatch-handling yang sama persis, tidak diduplikasi di sini). Kalau
    TIDAK ada alert OPEN, tetap catat intervention - satu POST tetap berarti
    ada perbaikan (docs/DECISIONS.md §25), hanya saja tidak ada alert yang
    perlu ditutup.
    """
    alert = open_alerts_by_item([item_id]).get(item_id)
    if alert is not None:
        result = resolve_with_intervention(alert["alert_id"], performed_at)
        return {"intervention": result["intervention"], "alert": result["alert"]}

    intervention_row = interventions.record_intervention(item_id, performed_at)
    return {"intervention": intervention_row, "alert": None}


def evaluate_and_open(frame: pd.DataFrame, scored_at: pd.Timestamp) -> list[int]:
    """Satu siklus evaluasi alert - dipanggil SEKALI per scheduled scoring
    run (predictive/scoring.py::run_and_persist()), bukan per request live.

    Langkah 0 (docs - klarifikasi user 2026-09-03): auto-resolve dulu semua
    alert OPEN yang cycle-nya SUDAH tertutup di data operasional (corrective/
    preventive work order yang berakhir dismantle - lihat
    auto_resolve_closed_cycles()). Item yang baru dilepas TIDAK MUNCUL lagi
    di `frame` (sudah bukan PART aktif), jadi ini dicek terpisah dari
    seluruh alert OPEN, bukan dari isi `frame`.

    Untuk tiap PART yang gate_flagged di `frame`: sinkron cycle-nya,
    lewati kalau sudah ada alert OPEN untuk episode yang sama, lewati kalau
    masih dalam masa suppression (kecuali emergency override), lalu buka
    alert baru.

    Return daftar alert_id yang baru dibuka pada run ini (TIDAK termasuk
    yang auto-resolved - lihat auto_resolve_closed_cycles() untuk itu).
    """
    auto_resolve_closed_cycles()

    flagged = frame.loc[frame["gate_flagged"]]
    opened_ids: list[int] = []

    for _, row in flagged.iterrows():
        item_id = row["item_id"]
        score = float(row["failure_probability_30d"])

        try:
            cycle = cycle_store.ensure_active_cycle(item_id)
        except cycle_store.ItemNotInstalled:
            continue
        cycle_id = cycle["cycle_id"]

        # terminal_id di sini = serial code fisik terminal (frame["terminal_label"]),
        # BUKAN ID internal terminal_inventory_item_id - sama seperti
        # predictive/scoring.py::record_predictions(), lihat WHY di sana.
        terminal_id = row.get("terminal_label")
        terminal_id = None if pd.isna(terminal_id) else str(terminal_id)
        part_type = row.get("item_model_code")
        part_type = None if pd.isna(part_type) else str(part_type)

        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cycle_id FROM predictive.item_cycle WHERE cycle_id = %s FOR UPDATE",
                    (cycle_id,),
                )
                next_seq = _next_intervention_seq(cur, cycle_id)

                cur.execute(
                    """
                    SELECT 1 FROM predictive.alert
                    WHERE item_id = %s AND cycle_id = %s AND intervention_seq = %s AND status = 'OPEN'
                    """,
                    (item_id, cycle_id, next_seq),
                )
                if cur.fetchone() is not None:
                    continue

                suppression = _active_suppression(cur, item_id, cycle_id)
                if suppression is not None:
                    _, previous_score = suppression
                    if not _emergency_override(score, previous_score):
                        continue

                cur.execute(
                    """
                    INSERT INTO predictive.alert
                        (terminal_id, part_type, item_id, cycle_id, intervention_seq,
                         opened_at, opened_score, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN')
                    RETURNING alert_id
                    """,
                    (
                        terminal_id, part_type, item_id, cycle_id, next_seq,
                        scored_at.to_pydatetime(), score,
                    ),
                )
                alert_id = cur.fetchone()[0]
            conn.commit()

        opened_ids.append(alert_id)

    return opened_ids


def resolve_with_intervention(alert_id: int, performed_at: pd.Timestamp) -> dict:
    """Jalur MANUAL untuk mematikan alert - untuk perbaikan yang TIDAK
    tercatat di data operasional (mis. sekadar mengencangkan baut). Kalau
    perbaikannya sudah tercatat di data operasional (work order corrective/
    preventive yang berakhir dismantle), alert mati sendiri lewat jalur
    OTOMATIS (auto_resolve_closed_cycles(), dipanggil dari
    evaluate_and_open()) - endpoint ini tidak perlu dipanggil untuk kasus
    itu, dan kalau tetap dipanggil, akan melihat alert ini sudah RESOLVED.

    Transaksi tunggal (docs §22 master prompt): validasi alert -> validasi
    cycle -> insert intervention -> resolve alert -> set suppression ->
    commit. Gagal di tengah = ROLLBACK, alert tidak pernah tersisa RESOLVED
    tanpa intervention atau sebaliknya.

    Tidak idempotent (docs/DECISIONS.md §28) - tidak ada identifier
    eksternal untuk dideteksi ulang, dipanggil lewat `resolve_by_item()`
    yang sudah memastikan hanya alert OPEN yang diproses.
    """
    alert = get_alert(alert_id)
    if alert is None:
        raise AlertNotFound(alert_id)
    if alert["status"] != "OPEN":
        raise AlertNotOpen(alert_id, alert["status"])

    # Sinkron cycle dari data operasional DI LUAR transaksi utama (transaksi
    # sendiri, sudah commit) - lihat WHY di cycles.py. Kalau item sudah
    # pindah cycle sejak alert ini dibuka, itu berarti cycle LAMA sudah
    # tertutup di data operasional (dismantle/failure/return) - auto-resolve
    # alert ini dulu (jalur OTOMATIS, docs - klarifikasi user), baru laporkan
    # ke pemanggil bahwa alert ini SUDAH selesai (bukan lewat intervention
    # yang baru saja dikirim).
    current_cycle = cycle_store.ensure_active_cycle(alert["item_id"])
    if current_cycle["cycle_id"] != alert["cycle_id"]:
        with db.connect() as conn:
            with conn.cursor() as cur:
                auto_resolved = _auto_resolve_if_cycle_closed(cur, alert)
            conn.commit()
        if auto_resolved is not None:
            raise AlertNotOpen(alert_id, auto_resolved["status"])
        raise AlertCycleMismatch(alert_id, alert["cycle_id"], current_cycle["cycle_id"])

    performed_at_value = (
        performed_at.to_pydatetime() if isinstance(performed_at, pd.Timestamp) else performed_at
    )
    suppression_until = (
        pd.Timestamp(performed_at_value) + pd.Timedelta(days=config.ALERT_SUPPRESSION_DAYS)
    ).to_pydatetime()

    with db.connect() as conn:
        with conn.cursor() as cur:
            # Kunci ulang baris alert DI DALAM transaksi (defends terhadap
            # race dengan resolve lain yang lolos pengecekan awal di atas).
            cur.execute(
                "SELECT status FROM predictive.alert WHERE alert_id = %s FOR UPDATE", (alert_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise AlertNotFound(alert_id)
            if row[0] != "OPEN":
                raise AlertNotOpen(alert_id, row[0])

            cur.execute(
                "SELECT cycle_id FROM predictive.item_cycle WHERE cycle_id = %s FOR UPDATE",
                (alert["cycle_id"],),
            )
            next_seq = _next_intervention_seq(cur, alert["cycle_id"])

            cur.execute(
                f"""
                INSERT INTO predictive.intervention
                    (item_id, cycle_id, intervention_seq, alert_id, performed_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {interventions._SELECT_COLUMNS}
                """,
                (alert["item_id"], alert["cycle_id"], next_seq, alert_id, performed_at_value),
            )
            intervention_row = interventions._row_to_dict(cur.fetchone())

            cur.execute(
                f"""
                UPDATE predictive.alert
                SET status = 'RESOLVED', resolved_at = %s, resolution_reason = 'INTERVENTION_RECORDED',
                    suppression_until = %s, updated_at = now()
                WHERE alert_id = %s
                RETURNING {_ALERT_SELECT_COLUMNS}
                """,
                (performed_at_value, suppression_until, alert_id),
            )
            alert_row = _row_to_alert(cur.fetchone())
        conn.commit()

    return {"intervention": intervention_row, "alert": alert_row}
