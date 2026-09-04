"""Bentuk request dan response API.

Nama field sengaja mengikuti apa yang benar-benar dikeluarkan model
(failure_probability_30d, ...) - tidak ada field yang dikarang dan tidak
ada yang diganti namanya, supaya jawaban API bisa dicocokkan langsung
dengan keluaran predict.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(protected_namespaces=(), extra="allow")


class HealthResponse(BaseModel):
    model_config = _CONFIG

    status: Literal["ok", "degraded"]
    api_version: str
    model_version: dict[str, str | None]
    database: Literal["reachable", "unreachable", "unchecked"]
    connection_pool: dict
    batch_cache: dict


class InspectionRequest(BaseModel):
    """Satu perbaikan yang dilaporkan aplikasi eksternal/teknisi terhadap
    satu PART, diidentifikasi lewat `host_serial_code`."""

    model_config = _CONFIG

    host_serial_code: str = Field(
        description="Label fisik PART (format MODEL-PAIRINGCODE-REPAIRSEQ)."
    )


class InspectionResult(BaseModel):
    model_config = _CONFIG

    inspection_id: int
    item_id: str
    cycle_id: str
    inspection_seq: int
    alert_id: int | None
    performed_at: str
    created_at: str


class AlertResult(BaseModel):
    model_config = _CONFIG

    alert_id: int
    terminal_serial_code: str | None
    part_type: str | None
    item_id: str
    host_serial_code: str | None
    cycle_id: str
    inspection_seq: int
    status: Literal["OPEN", "RESOLVED"]
    opened_at: str
    opened_score: float
    resolved_at: str | None
    resolution_reason: str | None
    suppression_until: str | None


class InspectionResponse(BaseModel):
    model_config = _CONFIG

    inspection: InspectionResult
    alert: AlertResult | None = Field(
        description="Alert yang ikut di-RESOLVE, kalau item ini sedang punya alert OPEN. null kalau tidak ada."
    )


class ErrorResponse(BaseModel):
    model_config = _CONFIG

    status: str
    message: str
    item_id: str | None = None
