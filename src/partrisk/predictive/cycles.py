"""Info siklus fisik operasional - dibaca dari core.data_reader.get_cycles()."""

from __future__ import annotations

from partrisk.core import data_reader

_STILL_ACTIVE_REASON = "RIGHT_CENSORED_AT_DATA_END"


class ItemNotInstalled(LookupError):
    """Item tidak ditemukan di data operasional, atau tidak sedang
    terpasang (tidak punya cycle aktif)."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(f"PART '{item_id}' tidak punya installation cycle aktif.")


def _cycle_no(cycle_id: str) -> int:
    return int(cycle_id.rsplit(":", 1)[-1])


def ensure_active_cycle(item_id: str) -> dict:
    """Cycle aktif item ini saat ini. Raise ItemNotInstalled kalau tidak ada."""
    data_end = data_reader.get_dataset_max_event_on()
    cycles = data_reader.get_cycles(item_id, data_end)
    active = cycles.loc[cycles["cycle_end_reason"] == _STILL_ACTIVE_REASON]
    if active.empty:
        raise ItemNotInstalled(item_id)

    row = active.iloc[-1]
    cycle_id = row["installation_cycle_id"]
    return {
        "cycle_id": cycle_id,
        "item_id": row["item_identifier_clean"],
        "cycle_no": _cycle_no(cycle_id),
        "started_at": row["installed_on"],
        "is_active": True,
    }


def cycle_status(item_id: str, cycle_id: str) -> dict | None:
    """Status satu cycle tertentu. Return None kalau tidak ditemukan."""
    data_end = data_reader.get_dataset_max_event_on()
    cycles = data_reader.get_cycles(item_id, data_end)
    match = cycles.loc[cycles["installation_cycle_id"] == cycle_id]
    if match.empty:
        return None

    row = match.iloc[0]
    is_active = row["cycle_end_reason"] == _STILL_ACTIVE_REASON
    return {
        "cycle_id": cycle_id,
        "is_active": is_active,
        "end_reason": None if is_active else row["cycle_end_reason"],
    }


def lock_item(cur, item_id: str) -> None:
    """Kunci transaksional per-item (Postgres advisory lock)."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (item_id,))
