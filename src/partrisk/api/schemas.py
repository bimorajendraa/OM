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


class InterventionRequest(BaseModel):
    """Satu perbaikan yang dilaporkan aplikasi eksternal/teknisi terhadap
    satu PART - lihat docs/DECISIONS.md §28. Diidentifikasi lewat
    `host_serial_code` (label fisik PART, BUKAN alert_id internal - aplikasi
    eksternal tidak pernah tahu alert_id). Tidak ada field lain - satu POST
    di sini SUDAH BERARTI satu perbaikan terjadi, waktunya diambil dari saat
    server menerima request."""

    model_config = _CONFIG

    host_serial_code: str = Field(
        description="Label fisik PART (format MODEL-PAIRINGCODE-REPAIRSEQ)."
    )


class InterventionResult(BaseModel):
    model_config = _CONFIG

    intervention_id: int
    item_id: str
    cycle_id: str
    intervention_seq: int
    alert_id: int | None
    performed_at: str
    created_at: str


class AlertResult(BaseModel):
    model_config = _CONFIG

    alert_id: int
    terminal_id: str | None
    part_type: str | None
    item_id: str
    cycle_id: str
    intervention_seq: int
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED", "SUPPRESSED"]
    opened_at: str
    opened_score: float
    resolved_at: str | None
    resolution_reason: str | None
    suppression_until: str | None


class InterventionResponse(BaseModel):
    model_config = _CONFIG

    intervention: InterventionResult
    alert: AlertResult | None = Field(
        description="Alert yang ikut di-RESOLVE, kalau item ini sedang punya alert OPEN. null kalau tidak ada."
    )


class ErrorResponse(BaseModel):
    model_config = _CONFIG

    status: str
    message: str
    item_id: str | None = None
