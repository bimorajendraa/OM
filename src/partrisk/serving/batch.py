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
from partrisk.engines import predict as death_risk
from partrisk.engines import predict as scrap_model
from partrisk.engines.survival import predict as predict_survival
from partrisk.serving import alerts as alert_store
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

    failure, snapshot, full_snapshot = _score_failure(cycles, events, episodes, data_end)
    scrap = _score_scrap(events, cycles, data_end, failure["item_id"])
    survival_advisory = _score_survival_advisory(full_snapshot, events, cycles, episodes, terminal_raw)

    frame = failure.merge(scrap, on="item_id", how="left")
    frame = frame.merge(survival_advisory, on="item_id", how="left")
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
            "scrap": scrap_model.load_scrap_model()[2]["model_version"],
        },
    )


def _score_failure(
    cycles: pd.DataFrame, events: pd.DataFrame, episodes: pd.DataFrame, data_end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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

    flagged = result.loc[result["gate_flagged"]]
    alert_store.register_flagged(
        flagged["item_id"], flagged["failure_probability_30d"],
        gate_threshold, metadata["model_version"],
    )
    result = alert_store.annotate(result)
    result["in_official_queue"] = result["gate_flagged"] | result["alert_status"].eq("OPEN")

    features_by_item = snapshot[serving.SOURCE_COLUMNS].copy()
    features_by_item.index = pd.Index(
        snapshot["item_identifier_clean"].to_numpy(), name="item_id"
    )
    full_snapshot = snapshot.drop(columns=config.DEGRADATION_FEATURES + config.LOCAL_DENSITY_FEATURES)
    return result, features_by_item, full_snapshot


def _score_survival_advisory(
    full_snapshot: pd.DataFrame, events: pd.DataFrame, cycles: pd.DataFrame,
    episodes: pd.DataFrame, terminal_raw: pd.DataFrame,
) -> pd.DataFrame:
    item_ids = full_snapshot["item_identifier_clean"].to_numpy()
    try:
        model, encoder, metadata, calibrators = predict_survival.load_model()
    except FileNotFoundError:
        return pd.DataFrame({
            "item_id": item_ids,
            "median_days_to_failure": np.nan,
            "days_until_survival_90pct": np.nan,
            "days_until_risk_medium": np.nan,
            "days_until_risk_high": np.nan,
        })
    return predict_survival.score_batch(
        full_snapshot, events, cycles, episodes, terminal_raw, model, encoder, metadata, calibrators
    )


def _score_scrap(
    events: pd.DataFrame,
    cycles: pd.DataFrame,
    data_end: pd.Timestamp,
    items: pd.Series,
) -> pd.DataFrame:
    model, calibrator, metadata = scrap_model.load_scrap_model()

    state = _scrap_states(events, cycles, data_end, items)
    if state.empty:
        return pd.DataFrame(columns=[
            "item_id", "item_type", "scrap_probability", "scrap_risk_level",
            "item_type_known_to_model",
        ])

    features = feature_builder.build_scrap_features(state, metadata["known_item_types"])
    raw = model.predict_proba(features)[:, 1]
    probability = calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]

    cutoffs = metadata["risk_cutoffs"]
    return pd.DataFrame({
        "item_id": state["item_identifier_clean"].to_numpy(),
        "item_type": state["item_type_clean"].to_numpy(),
        "scrap_probability": np.round(probability, 4),
        "scrap_risk_level": [
            scrap_model.risk_level(value, cutoffs) for value in probability
        ],
        "item_type_known_to_model": state["item_type_clean"]
        .isin(metadata["known_item_types"])
        .to_numpy(),
    })


def _scrap_states(
    events: pd.DataFrame,
    cycles: pd.DataFrame,
    data_end: pd.Timestamp,
    items: pd.Series,
) -> pd.DataFrame:
    moment = pd.Timestamp(data_end)
    wanted = pd.Index(pd.unique(pd.Series(items)))

    seen = events.loc[events["item_identifier_clean"].isin(wanted)].copy()
    seen["created_on"] = pd.to_datetime(seen["created_on"])
    seen = seen.loc[seen["created_on"] <= moment]
    if seen.empty:
        return pd.DataFrame()

    status = seen["status_clean"].fillna("")
    seen["_is_repaired"] = status.eq(config.REPAIR_COMPLETED_STATUS)
    seen["_is_failure"] = seen["is_failure_onset"].fillna(False).astype(bool)

    grouped = seen.groupby("item_identifier_clean", sort=False)
    state = pd.DataFrame({
        "item_type_clean": grouped["item_type_clean"].last(),
        "first_seen_on": grouped["created_on"].min(),
        "prior_repaired_count": grouped["_is_repaired"].sum().astype("int64"),
        "prior_failure_count": grouped["_is_failure"].sum().astype("int64"),
    })

    installs = cycles.loc[cycles["item_identifier_clean"].isin(wanted)].copy()
    installs["installed_on"] = pd.to_datetime(installs["installed_on"])
    installs = installs.loc[installs["installed_on"] <= moment]
    last_install = installs.groupby("item_identifier_clean")["installed_on"].max()

    state = state.reindex(wanted.intersection(state.index))
    state["failure_onset_on"] = moment
    state["age_total_days"] = (
        moment - state["first_seen_on"]
    ).dt.total_seconds() / 86400.0
    state["cycle_age_days"] = (
        moment - pd.DatetimeIndex(state.index.map(last_install))
    ).total_seconds() / 86400.0
    return state.reset_index(names="item_identifier_clean")


def _attach_context(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    known = events.loc[events["place_canonical_clean"].notna()]
    location = known.groupby("item_identifier_clean")["place_canonical_clean"].last()
    frame["location"] = frame["item_id"].map(location)
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
    horizon = config.TARGET_HORIZON_DAYS
    scrap_levels = [
        None if pd.isna(level) else level for level in frame["scrap_risk_level"]
    ]
    decisions = [
        serving.recommend(failure_level, scrap_level)
        for failure_level, scrap_level in zip(frame["failure_risk_level"], scrap_levels)
    ]
    frame["priority"] = [decision["priority"] for decision in decisions]
    frame["recommended_action"] = [decision["action"] for decision in decisions]
    frame["recommendation_message"] = [decision["message"] for decision in decisions]
    frame["replacement_candidate"] = [
        serving.is_replacement_candidate(failure_level, scrap_level)
        for failure_level, scrap_level in zip(frame["failure_risk_level"], scrap_levels)
    ]
    frame[f"death_probability_{horizon}d"] = death_risk.death_probability(
        frame[f"failure_probability_{horizon}d"], frame["scrap_probability"]
    )
    return frame


def filter_scores(
    frame: pd.DataFrame,
    risk: str | None = None,
    priority: str | None = None,
    item_type: str | None = None,
    client: str | None = None,
    location: str | None = None,
    terminal_id: str | None = None,
    search: str | None = None,
    replacement_candidates_only: bool = False,
    official_queue_only: bool = True,
) -> pd.DataFrame:
    result = frame

    if official_queue_only:
        result = result[result["in_official_queue"]]
    if search:
        result = result[
            result["item_id"].str.contains(search.strip().upper(), regex=False, na=False)
        ]
    if risk:
        result = result[result["failure_risk_level"].eq(risk.upper())]
    if priority:
        result = result[result["priority"].eq(priority.upper())]
    if item_type:
        result = result[result["item_type"].fillna("").str.upper().eq(item_type.upper())]
    if client:
        result = result[result["client"].fillna("").str.upper().eq(client.upper())]
    if location:
        result = result[result["location"].fillna("").str.upper().eq(location.upper())]
    if terminal_id:
        result = result[result["terminal_id"].astype("string").eq(str(terminal_id))]
    if replacement_candidates_only:
        result = result[result["replacement_candidate"]]
    return result


def summary(frame: pd.DataFrame) -> dict:
    levels = frame["failure_risk_level"].value_counts()
    return {
        "active_parts": int(len(frame)),

        "official_queue_size": int(frame["in_official_queue"].sum()),
        "high_risk_parts": int(levels.get("HIGH", 0)),
        "medium_risk_parts": int(levels.get("MEDIUM", 0)),
        "low_risk_parts": int(levels.get("LOW", 0)),
        "replacement_candidates": int(frame["replacement_candidate"].sum()),
        "priority_counts": {
            str(name): int(count)
            for name, count in frame["priority"].value_counts().items()
        },

        "expected_failures_by_horizon": {
            f"{days}d": round(float(frame[f"failure_probability_{days}d"].sum()), 1)
            for days in config.PREDICTION_HORIZON_DAYS
        },
    }


def location_summary(frame: pd.DataFrame) -> pd.DataFrame:
    known = frame.loc[frame["location"].notna()]
    grouped = known.groupby("location").agg(
        active_parts=("item_id", "count"),
        high_risk_parts=("failure_risk_level", lambda s: int((s == "HIGH").sum())),
        medium_risk_parts=("failure_risk_level", lambda s: int((s == "MEDIUM").sum())),
        replacement_candidates=("replacement_candidate", "sum"),
    )
    grouped["replacement_candidates"] = grouped["replacement_candidates"].astype(int)
    return grouped.sort_values("high_risk_parts", ascending=False)


_TERMINAL_COLUMNS = [
    "terminal_id", "terminal_label", "terminal_model_name", "active_parts",
    "high_risk_parts", "medium_risk_parts", "low_risk_parts",
    "top_risk_item_id", "top_risk_probability", "nearest_median_days_to_failure", "location",
    "replacement_candidates",
]


def terminal_overview(frame: pd.DataFrame) -> dict:
    return {
        "terminals": int(frame.loc[frame["terminal_id"].notna(), "terminal_id"].nunique()),
        "parts_with_terminal": int(frame["terminal_id"].notna().sum()),
        "parts_without_terminal": int(frame["terminal_id"].isna().sum()),
    }


def terminal_summary(frame: pd.DataFrame) -> pd.DataFrame:

    known = frame.loc[frame["terminal_id"].notna()].copy()
    if known.empty:
        return pd.DataFrame(columns=_TERMINAL_COLUMNS).set_index("terminal_id")

    prob_column = f"failure_probability_{config.TARGET_HORIZON_DAYS}d"
    grouped = known.groupby("terminal_id").agg(
        terminal_label=("terminal_label", "first"),
        terminal_model_name=("terminal_model_name", "first"),
        location=("location", "first"),
        active_parts=("item_id", "count"),
        high_risk_parts=("failure_risk_level", lambda s: int((s == "HIGH").sum())),
        medium_risk_parts=("failure_risk_level", lambda s: int((s == "MEDIUM").sum())),
        low_risk_parts=("failure_risk_level", lambda s: int((s == "LOW").sum())),
        replacement_candidates=("replacement_candidate", "sum"),
    )
    grouped["replacement_candidates"] = grouped["replacement_candidates"].astype(int)

    top_risk = (
        known.sort_values("tier_score", ascending=False)
        .groupby("terminal_id")
        .first()[["item_id", prob_column]]
        .rename(columns={"item_id": "top_risk_item_id", prob_column: "top_risk_probability"})
    )
    nearest = (
        known.dropna(subset=["median_days_to_failure"])
        .sort_values("median_days_to_failure", ascending=True)
        .groupby("terminal_id")
        .first()[["median_days_to_failure"]]
        .rename(columns={"median_days_to_failure": "nearest_median_days_to_failure"})
    )

    grouped = grouped.join(top_risk).join(nearest)
    return grouped.sort_values(
        ["high_risk_parts", "medium_risk_parts"], ascending=False
    )


def facets(frame: pd.DataFrame) -> dict[str, list[str]]:
    def values(column: str) -> list[str]:
        return sorted(frame[column].dropna().astype(str).unique().tolist())

    return {
        "risk_levels": ["HIGH", "MEDIUM", "LOW"],
        "priorities": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        "item_types": values("item_type"),
        "clients": values("client"),
        "locations": values("location"),
    }
