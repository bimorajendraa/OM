from __future__ import annotations

import concurrent.futures
import functools
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import psycopg

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.engines import predict as failure_model
from partrisk.serving import single as serving

logger = logging.getLogger(__name__)


_CACHEABLE = (
    "get_dataset_max_event_on", "get_events", "get_cycles",
    "get_failure_episodes", "get_terminal_context",
)

_local = threading.local()
_installed = False


def _scope() -> dict | None:
    return getattr(_local, "scope", None)


def _wrap(name: str, original):
    @functools.wraps(original)
    def reader(*args, **kwargs):
        scope = _scope()
        if scope is None:
            return original(*args, **kwargs)

        key = (name, args, tuple(sorted(kwargs.items())))
        if key not in scope:
            scope[key] = original(*args, **kwargs)
        return scope[key]

    reader.__wrapped_by_query_cache__ = True
    return reader


def install() -> None:
    global _installed
    if _installed:
        return
    for name in _CACHEABLE:
        original = getattr(data_reader, name)
        if getattr(original, "__wrapped_by_query_cache__", False):
            continue
        setattr(data_reader, name, _wrap(name, original))
    _installed = True


@contextmanager
def request_scope():
    install()
    if _scope() is not None:
        yield
        return

    _local.scope = {}
    try:
        yield
    finally:
        _local.scope = None


def reads_in_scope() -> int:
    scope = _scope()
    return 0 if scope is None else len(scope)


_DATA_STATE_LOCK = threading.RLock()
_data_end: pd.Timestamp | None = None
_checked_at: float = 0.0
_generation: int = 0


def current_data_end(force_refresh: bool = False) -> pd.Timestamp:
    global _data_end, _checked_at, _generation

    with _DATA_STATE_LOCK:
        now = time.time()
        fresh_enough = (
            _data_end is not None
            and now - _checked_at < serving.DATA_FRESHNESS_TTL_SECONDS
        )
        if fresh_enough and not force_refresh:
            return _data_end

        latest = data_reader.get_dataset_max_event_on()
        _checked_at = now

        if _data_end is not None and latest != _data_end:
            logger.info("Data bertambah: %s -> %s. Potret armada dibuang.", _data_end, latest)
            failure_model.clear_fleet_cache()
            _generation += 1

        _data_end = latest
        return _data_end


def generation() -> int:
    with _DATA_STATE_LOCK:
        return _generation


def reset() -> None:
    global _data_end, _checked_at, _generation
    with _DATA_STATE_LOCK:
        _data_end = None
        _checked_at = 0.0
        _generation = 0
        failure_model.clear_fleet_cache()


_HORIZONS = config.PREDICTION_HORIZON_DAYS


@dataclass
class BatchScores:
    frame: pd.DataFrame
    snapshot: pd.DataFrame
    data_end: pd.Timestamp
    model_version: dict
    generation: int = 0
    computed_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.computed_at

    @property
    def scored_at(self) -> dict:
        return {
            "data_through": str(self.data_end),
            "computed_seconds_ago": int(self.age_seconds),
            "model_version": self.model_version,
        }

    def is_stale(self, generation: int) -> bool:
        return (
            self.age_seconds > serving.BATCH_CACHE_TTL_SECONDS
            or generation != self.generation
        )


_CACHE: BatchScores | None = None
_BATCH_LOCK = threading.Lock()


def score_active_parts(force_refresh: bool = False) -> BatchScores:
    global _CACHE
    generation_value = generation() if _CACHE is None else _fresh_generation()
    with _BATCH_LOCK:
        if force_refresh or _CACHE is None or _CACHE.is_stale(generation_value):
            _CACHE = _compute(generation_value)
        return _CACHE


def _fresh_generation() -> int:
    current_data_end()
    return generation()


def cached_scores() -> BatchScores | None:
    return _CACHE


def _fetch_batch_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        cycles_future = executor.submit(data_reader.get_cycles)
        events_future = executor.submit(data_reader.get_events)
        episodes_future = executor.submit(data_reader.get_failure_episodes)
        terminal_future = executor.submit(data_reader.get_terminal_context)
        return (
            cycles_future.result(), events_future.result(),
            episodes_future.result(), terminal_future.result(),
        )


def _compute(generation_value: int) -> BatchScores:
    current_data_end()
    try:
        cycles, events, episodes, terminal_raw = _fetch_batch_inputs()
    except psycopg.Error as error:
        raise serving.DataSourceUnavailable(
            f"Database tidak bisa dibaca ({type(error).__name__})."
        ) from error

    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())

    failure, snapshot = _score_failure(cycles, events, episodes, data_end)

    frame = failure.copy()
    frame = _attach_context(frame, events)
    frame = _attach_terminal(frame, terminal_raw)
    frame = _attach_recommendation(frame)
    frame = frame.sort_values("tier_score", ascending=False).reset_index(drop=True)
    frame.insert(0, "rank", np.arange(1, len(frame) + 1))

    return BatchScores(
        frame=frame,
        snapshot=snapshot,
        data_end=data_end,
        generation=generation_value,
        model_version={
            "failure": failure_model.load_failure_model()[2]["model_version"],
        },
    )


_SOURCE_COLUMNS = [
    "item_model_code_clean",
    "days_since_installation",
    "total_prior_events",
    "prior_failure_count",
    "prior_failure_365d",
    "prior_corrective_count",
    "prior_corrective_30d",
    "days_since_last_corrective",
    "prior_distinct_places",
    "previous_cycle_lifetime_mean",
    "has_previous_cycle",
    "log_model_failures_90d",
    "model_failure_rate_90d",
    "log_model_fleet_size",
]


def _score_failure(
    cycles: pd.DataFrame, events: pd.DataFrame, episodes: pd.DataFrame, data_end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    model, calibrator, metadata = failure_model.load_failure_model()

    snapshot = feature_builder.current_observations(cycles, events)
    snapshot = feature_builder.attach_history(snapshot, events)
    snapshot = feature_builder.attach_degradation_history(snapshot, cycles, events)
    snapshot = feature_builder.attach_fleet_snapshot(
        snapshot, failure_model.fleet_snapshot(data_end)
    )
    snapshot = feature_builder.attach_item_type_density_snapshot(
        snapshot, events, feature_builder.item_type_density_snapshot(cycles, events, episodes, data_end)
    )
    support = feature_builder.part_model_support(
        snapshot, metadata["part_model_support"]
    )

    steps = max(_HORIZONS) // config.OBSERVATION_STEP_DAYS
    survival = np.ones(len(snapshot), dtype=float)
    tier_score = np.zeros(len(snapshot), dtype=float)
    cumulative: dict[int, np.ndarray] = {}
    for step in range(steps):
        features = feature_builder.project_features(snapshot, support, step)
        features = features[metadata["features"]]
        raw = model.predict_proba(features)[:, 1]
        if step == 0:
            tier_score = raw
        hazard = calibrator.predict(raw)
        survival = survival * (1.0 - hazard)
        cumulative[(step + 1) * config.OBSERVATION_STEP_DAYS] = 1.0 - survival

    cutoffs = metadata["risk_cutoffs"]
    result = pd.DataFrame({
        "item_id": snapshot["item_identifier_clean"].to_numpy(),
        "item_model_code": snapshot["item_model_code_clean"].to_numpy(),
        "client": snapshot["installed_client_clean"].to_numpy(),
        "installation_age_days": snapshot["days_since_installation"].round(1).to_numpy(),
        "tier_score": tier_score,
    })
    for days in _HORIZONS:
        result[f"failure_probability_{days}d"] = np.round(cumulative[days], 4)
    result["failure_risk_level"] = [
        failure_model.risk_level(score, cutoffs)
        for score in result["failure_probability_30d"]
    ]

    gate_info = metadata.get("gate")
    gate_threshold = (
        gate_info["threshold"]
        if gate_info and gate_info.get("feasible") and gate_info.get("horizon_days") == config.TARGET_HORIZON_DAYS
        else None
    )
    result["gate_flagged"] = (
        result["failure_probability_30d"] >= gate_threshold if gate_threshold is not None else False
    )

    features_by_item = snapshot[_SOURCE_COLUMNS].copy()
    features_by_item.index = pd.Index(
        snapshot["item_identifier_clean"].to_numpy(), name="item_id"
    )
    return result, features_by_item


def _attach_context(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    known_location = events.loc[events["place_canonical_clean"].notna()]
    location = known_location.groupby("item_identifier_clean")["place_canonical_clean"].last()
    frame["location"] = frame["item_id"].map(location)

    known_type = events.loc[events["item_type_clean"].notna()]
    item_type = known_type.groupby("item_identifier_clean")["item_type_clean"].last()
    frame["item_type"] = frame["item_id"].map(item_type)
    return frame


_RELIABLE_TERMINAL_LINK_STATUSES = frozenset({
    "VALID_POINT_IN_TIME_RELATION", "VALID_RELATION_RECORDED_AFTER_INSTALLATION",
})


def _attach_terminal(frame: pd.DataFrame, terminal_raw: pd.DataFrame) -> pd.DataFrame:
    reliable = terminal_raw.loc[
        terminal_raw["parent_link_quality_status"].isin(_RELIABLE_TERMINAL_LINK_STATUSES)
        & terminal_raw["terminal_inventory_item_id"].notna()
    ]

    latest = reliable.groupby("item_identifier_clean").last()

    terminal_ids = latest["terminal_inventory_item_id"].astype("Int64")
    frame["terminal_id"] = frame["item_id"].map(terminal_ids).astype("string")
    frame["terminal_label"] = frame["item_id"].map(latest["terminal_serial_code_clean"])
    frame["terminal_model_name"] = frame["item_id"].map(latest["terminal_model_name_clean"])
    return frame


def _attach_recommendation(frame: pd.DataFrame) -> pd.DataFrame:
    decisions = [serving.recommend(failure_level) for failure_level in frame["failure_risk_level"]]
    frame["priority"] = [decision["priority"] for decision in decisions]
    frame["recommended_action"] = [decision["action"] for decision in decisions]
    frame["recommendation_message"] = [decision["message"] for decision in decisions]
    return frame
