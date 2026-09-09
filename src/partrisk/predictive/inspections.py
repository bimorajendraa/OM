from __future__ import annotations

_COLUMNS = (
    "inspection_id", "item_serial_code", "inspection_seq", "prediction_id",
    "created_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))
