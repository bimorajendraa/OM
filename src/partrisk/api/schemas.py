from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    model_config = _REQUEST_CONFIG

    host_serial_code: str = Field(
        description="Label fisik PART (format MODEL-PAIRINGCODE-REPAIRSEQ)."
    )


class InspectionResult(BaseModel):
    model_config = _CONFIG

    inspection_id: int
    item_serial_code: str
    inspection_seq: int
    prediction_id: int
    created_at: str


class AlertResult(BaseModel):
    model_config = _CONFIG

    prediction_id: int
    terminal_serial_code: str | None
    item_serial_code: str
    p30: float
    scored_at: str


class InspectionResponse(BaseModel):
    model_config = _CONFIG

    inspection: InspectionResult
    alert: AlertResult | None = Field(
        description="Alert yang ikut di-RESOLVE, kalau item ini sedang punya alert OPEN. null kalau tidak ada."
    )
