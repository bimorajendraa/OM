"""Bentuk request dan response API.

Nama field sengaja mengikuti apa yang benar-benar dikeluarkan model
(failure_probability_30d, scrap_probability, ...) - tidak ada field yang
dikarang dan tidak ada yang diganti namanya, supaya jawaban API bisa
dicocokkan langsung dengan keluaran predict.py / predict_scrap.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(protected_namespaces=(), extra="allow")

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ScoringStatus = Literal["SCORED", "NOT_SCORABLE"]


class HealthResponse(BaseModel):
    model_config = _CONFIG

    status: Literal["ok", "degraded"]
    api_version: str
    model_version: dict[str, str | None]
    database: Literal["reachable", "unreachable", "unchecked"]
    connection_pool: dict
    batch_cache: dict


class SurvivalPoint(BaseModel):
    """Satu titik kurva S(t) model survival - lihat `survival_curve` di
    `FailurePrediction`."""

    model_config = _CONFIG

    days_from_now: int
    survival_probability: float = Field(ge=0.0, le=1.0)


class FailurePrediction(BaseModel):
    """Keluaran predict.predict() apa adanya, DITAMBAH field advisory dari
    model survival event-based (mode aditif - lihat docs/DECISIONS.md §1,
    `partrisk.predict_survival`, model TERPISAH, TIDAK menentukan
    failure_probability_*/risk_level di atas).

    Angkanya adalah PELUANG kerusakan dalam N hari ke depan. Model tidak
    memperkirakan tanggal kerusakan pasti - field advisory di bawah
    (median_days_to_failure dkk) menjawab pertanyaan "kapan" yang secara
    struktural tidak bisa dijawab model klasifikasi 30-hari di atas, tapi
    SERING None (lihat median_days_to_failure_basis) - kurva survival butuh
    waktu lama untuk turun sampai separuh, dan mayoritas PART aktif belum
    setua itu.
    """

    model_config = _CONFIG

    item_id: str
    failure_probability_30d: float = Field(ge=0.0, le=1.0)
    failure_probability_60d: float = Field(ge=0.0, le=1.0)
    failure_probability_90d: float = Field(ge=0.0, le=1.0)
    failure_probability_120d: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    model_version: str
    as_of: str
    installed_on: str
    median_days_to_failure: float | None = None
    median_days_to_failure_basis: str | None = None
    days_until_survival_90pct: float | None = None
    days_until_risk_medium: float | None = None
    days_until_risk_high: float | None = None
    survival_curve: list[SurvivalPoint] | None = None
    curve_step_days: int | None = None
    curve_horizon_days: int | None = None
    curve_is_calibrated: bool = False
    survival_risk_30d: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_risk_60d: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_risk_90d: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_risk_120d: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_risk_is_calibrated: bool = False


class ScrapPrediction(BaseModel):
    """Keluaran predict_scrap.predict_scrap() apa adanya.

    BERSYARAT: peluang PART tidak bisa diperbaiki JIKA rusak - bukan peluang
    PART ini rusak.
    """

    model_config = _CONFIG

    item_id: str
    scrap_probability: float = Field(ge=0.0, le=1.0)
    scrap_risk_level: RiskLevel
    scrap_risk_basis: str
    item_type: str | None = None
    item_type_known_to_model: bool
    model_version: str
    as_of: str


class Recommendation(BaseModel):
    model_config = _CONFIG

    priority: Priority
    action: str
    message: str
    based_on: dict


class RiskFactor(BaseModel):
    model_config = _CONFIG

    code: str
    direction: Literal["RISK_FACTOR", "MITIGATING", "CONTEXT"]
    label: str
    value: float | int | None = None


class Explanation(BaseModel):
    model_config = _CONFIG

    disclaimer: str
    factors: list[RiskFactor]
    notes: list[str] = []
    caveats: list[str] = []


class FailureResponse(BaseModel):
    model_config = _CONFIG

    item_id: str
    status: ScoringStatus
    reason: str | None = None
    failure: FailurePrediction | None = None


class ScrapResponse(BaseModel):
    model_config = _CONFIG

    item_id: str
    status: ScoringStatus
    reason: str | None = None
    scrap: ScrapPrediction | None = None


class AssessmentResponse(BaseModel):
    model_config = _CONFIG

    item_id: str
    status: ScoringStatus
    reason: str | None = None
    as_of: str | None = None
    failure: FailurePrediction | None = None
    scrap: ScrapPrediction | None = None
    death_probability_30d: float | None = None
    recommendation: Recommendation | None = None
    replacement_candidate: bool | None = None
    explanation: Explanation | None = None
    model_version: dict[str, str | None] | None = None


class PriorityItem(BaseModel):
    """Satu baris daftar prioritas hasil batch scoring."""

    model_config = _CONFIG

    rank: int
    item_id: str
    item_type: str | None = None
    item_model_code: str | None = None
    client: str | None = None
    location: str | None = None
    terminal_id: str | None = None
    terminal_label: str | None = None
    terminal_model_name: str | None = None
    installation_age_days: float | None = None
    failure_probability_30d: float
    failure_probability_60d: float
    failure_probability_90d: float
    failure_probability_120d: float
    failure_risk_level: RiskLevel
    scrap_probability: float | None = None
    scrap_risk_level: RiskLevel | None = None
    death_probability_30d: float | None = None
    priority: Priority
    recommended_action: str
    recommendation_message: str
    replacement_candidate: bool
    gate_flagged: bool
    alert_status: Literal["OPEN"] | None = None
    alert_opened_at: str | None = None
    alert_score_at_open: float | None = None
    alert_threshold_at_open: float | None = None
    alert_model_version: str | None = None
    median_days_to_failure: float | None = None
    days_until_survival_90pct: float | None = None
    days_until_risk_medium: float | None = None
    days_until_risk_high: float | None = None


class ScoredAt(BaseModel):
    """Kapan daftar ini dihitung dan sampai kapan datanya."""

    model_config = _CONFIG

    data_through: str
    computed_seconds_ago: int
    model_version: dict[str, str]


class RecommendationListResponse(BaseModel):
    model_config = _CONFIG

    total: int
    returned: int
    offset: int
    scored_at: ScoredAt
    items: list[PriorityItem]


class OverviewResponse(BaseModel):
    model_config = _CONFIG

    summary: dict
    scored_at: ScoredAt
    top_priority: list[PriorityItem]


class FiltersResponse(BaseModel):
    model_config = _CONFIG

    risk_levels: list[str]
    priorities: list[str]
    item_types: list[str]
    clients: list[str]
    locations: list[str]


class FailureHistoryItem(BaseModel):
    model_config = _CONFIG

    date: str
    location: str | None = None
    status: str
    wo_type: str | None = None


class LocationHistoryItem(BaseModel):
    model_config = _CONFIG

    location: str
    first_seen: str
    last_seen: str
    events: int


class HistoryResponse(BaseModel):
    model_config = _CONFIG

    item_id: str
    failures: list[FailureHistoryItem]
    locations: list[LocationHistoryItem]


class ResolvedLocation(BaseModel):
    model_config = _CONFIG

    location: str
    lat: float
    lon: float
    active_parts: int
    high_risk_parts: int
    medium_risk_parts: int
    replacement_candidates: int


class UnresolvedLocation(BaseModel):
    model_config = _CONFIG

    location: str
    active_parts: int
    high_risk_parts: int
    medium_risk_parts: int
    replacement_candidates: int
    checked: bool


class LocationMapResponse(BaseModel):
    model_config = _CONFIG

    resolved: list[ResolvedLocation]
    unresolved: list[UnresolvedLocation]
    scored_at: ScoredAt


class TerminalSummaryItem(BaseModel):
    """Ringkasan satu Terminal - hasil AGREGASI prediction per-PART yang
    sudah ada (docs/DECISIONS.md), bukan model/skor baru khusus terminal."""

    model_config = _CONFIG

    terminal_id: str
    terminal_label: str | None = None
    terminal_model_name: str | None = None
    location: str | None = None
    active_parts: int
    high_risk_parts: int
    medium_risk_parts: int
    low_risk_parts: int
    top_risk_item_id: str | None = None
    top_risk_probability: float | None = None
    nearest_median_days_to_failure: float | None = None
    replacement_candidates: int


class TerminalListResponse(BaseModel):
    model_config = _CONFIG

    terminals: list[TerminalSummaryItem]
    terminals_total: int
    parts_with_terminal: int
    parts_without_terminal: int
    scored_at: ScoredAt


class ResolveAlertResponse(BaseModel):
    model_config = _CONFIG

    item_id: str
    status: Literal["RESOLVED"]


class ErrorResponse(BaseModel):
    model_config = _CONFIG

    status: str
    message: str
    item_id: str | None = None
