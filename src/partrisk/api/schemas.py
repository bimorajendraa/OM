"""Bentuk request dan response API.

Nama field sengaja mengikuti apa yang benar-benar dikeluarkan model
(failure_probability_30d, ...) - tidak ada field yang dikarang dan tidak
ada yang diganti namanya, supaya jawaban API bisa dicocokkan langsung
dengan keluaran predict.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONFIG = ConfigDict(protected_namespaces=(), extra="allow")

_REQUEST_CONFIG = ConfigDict(protected_namespaces=(), extra="forbid")


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

    model_config = _REQUEST_CONFIG

    host_serial_code: str = Field(
        description="Label fisik PART (format MODEL-PAIRINGCODE-REPAIRSEQ)."
    )
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Kunci retry-safety dari aplikasi pemanggil (opsional) - kirim ulang "
            "request dengan nilai yang SAMA dianggap request yang sama dan tidak "
            "akan membuat inspection kedua."
        ),
    )

    @field_validator("idempotency_key")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """String kosong/whitespace dianggap "tidak ada kunci" - kalau tidak,
        dua request tak terkait yang sama-sama kirim "" akan salah dianggap
        idempotent terhadap satu sama lain (lihat inspections.find_by_idempotency_key)."""
        if value is None:
            return None
        value = value.strip()
        return value or None


class InspectionResult(BaseModel):
    model_config = _CONFIG

    inspection_id: int
    item_id: str
    host_serial_code: str
    inspection_seq: int
    alert_id: int | None
    idempotency_key: str | None
    created_at: str


class AlertResult(BaseModel):
    model_config = _CONFIG

    alert_id: int
    terminal_serial_code: str | None
    item_id: str
    host_serial_code: str
    inspection_seq: int
    status: Literal["OPEN", "RESOLVED"]
    opened_at: str
    opened_score: float
    resolved_at: str | None
    suppression_until: str | None


class InspectionResponse(BaseModel):
    model_config = _CONFIG

    inspection: InspectionResult
    alert: AlertResult | None = Field(
        description="Alert yang ikut di-RESOLVE, kalau item ini sedang punya alert OPEN. null kalau tidak ada."
    )
