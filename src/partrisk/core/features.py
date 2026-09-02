from __future__ import annotations

import numpy as np
import pandas as pd

from partrisk.core import config

_DAY = np.timedelta64(1, "D")


def _log1p(values: pd.Series) -> pd.Series:
    return np.log1p(pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=0.0))


def _age_band(days: pd.Series) -> pd.Series:
    index = np.searchsorted(
        config.AGE_BAND_THRESHOLDS,
        pd.to_numeric(days, errors="coerce").fillna(0.0).to_numpy(),
        side="right",
    )
    return pd.Series(np.asarray(config.AGE_BAND_LABELS)[index], index=days.index)


def cumulative_support(observations: pd.DataFrame) -> pd.Series:
    times = observations["observation_on"].to_numpy("datetime64[ns]")
    support = np.zeros(len(observations), dtype="int64")
    grouped = observations.groupby("item_model_code_clean", sort=False, dropna=False)
    for rows in grouped.indices.values():
        support[rows] = np.searchsorted(np.sort(times[rows]), times[rows], side="right")
    return pd.Series(support, index=observations.index)


def support_totals(observations: pd.DataFrame) -> dict[str, int]:
    totals = observations.groupby("item_model_code_clean").size()
    return {str(model): int(count) for model, count in totals.items()}


def part_model_support(raw: pd.DataFrame, support_by_model: dict[str, int]) -> pd.Series:
    return (
        raw["item_model_code_clean"]
        .map(support_by_model)
        .fillna(0)
        .astype("int64")
    )


_HISTORY_COUNTS = [
    "total_prior_events",
    "prior_failure_count",
    "prior_corrective_count",
    "prior_corrective_30d",
    "prior_failure_365d",
    "prior_events_180d",
    "prior_distinct_places",
]


def attach_history(observations: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    observations = observations.reset_index(drop=True)
    total = len(observations)
    counts = {name: np.zeros(total, dtype="int64") for name in _HISTORY_COUNTS}
    last_corrective = np.full(total, np.datetime64("NaT"), dtype="datetime64[ns]")
    observed_at = observations["observation_on"].to_numpy("datetime64[ns]")

    if total and len(events):
        events = events.sort_values(
            ["item_identifier_clean", "created_on"], kind="stable"
        )
        event_times = events["created_on"].to_numpy("datetime64[ns]")
        is_corrective = events["wo_type_clean"].eq("CORRECTIVE").to_numpy()
        is_failure = events["is_failure_onset"].fillna(False).to_numpy(dtype=bool)
        is_new_place = _first_occurrence(events)
        event_rows = events.groupby("item_identifier_clean", sort=False).indices

        window_30 = np.timedelta64(30, "D")
        window_180 = np.timedelta64(180, "D")
        window_365 = np.timedelta64(365, "D")

        for item, rows in observations.groupby(
            "item_identifier_clean", sort=False
        ).indices.items():
            slot = event_rows.get(item)
            if slot is None:
                continue
            times = event_times[slot]
            cumulative_failure = np.cumsum(is_failure[slot])
            cumulative_corrective = np.cumsum(is_corrective[slot])
            cumulative_place = np.cumsum(is_new_place[slot])
            corrective_times = times[is_corrective[slot]]

            at = observed_at[rows]
            seen = np.searchsorted(times, at, side="right")
            seen_30 = np.searchsorted(times, at - window_30, side="right")
            seen_180 = np.searchsorted(times, at - window_180, side="right")
            seen_365 = np.searchsorted(times, at - window_365, side="right")

            failure_to_date = _at(cumulative_failure, seen)
            corrective_to_date = _at(cumulative_corrective, seen)

            counts["total_prior_events"][rows] = seen
            counts["prior_failure_count"][rows] = failure_to_date
            counts["prior_corrective_count"][rows] = corrective_to_date
            counts["prior_distinct_places"][rows] = _at(cumulative_place, seen)
            counts["prior_events_180d"][rows] = seen - seen_180
            counts["prior_corrective_30d"][rows] = corrective_to_date - _at(
                cumulative_corrective, seen_30
            )
            counts["prior_failure_365d"][rows] = failure_to_date - _at(
                cumulative_failure, seen_365
            )

            has_corrective = corrective_to_date > 0
            if has_corrective.any():
                position = np.maximum(corrective_to_date - 1, 0)
                last_corrective[rows] = np.where(
                    has_corrective, corrective_times[position], np.datetime64("NaT")
                )

    for name, values in counts.items():
        observations[name] = values
    observations["days_since_last_corrective"] = (observed_at - last_corrective) / _DAY
    return observations


def attach_degradation_history(
    observations: pd.DataFrame, cycles: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    from partrisk.core import features_survival

    observations = observations.reset_index(drop=True)

    cum = features_survival.cumulative_cycle_age(cycles)
    cum_lookup = cum.set_index("installation_cycle_id")
    matched = observations["installation_cycle_id"].map(cum_lookup["cumulative_prior_cycle_days"])
    count_matched = observations["installation_cycle_id"].map(cum_lookup["previous_cycle_count"])
    trend = features_survival.corrective_degradation_trend(observations, events)
    windowed = features_survival.windowed_corrective_extra(observations, events)

    observations["log_cumulative_prior_cycle_days"] = np.log1p(
        pd.to_numeric(matched, errors="coerce").fillna(0.0).clip(lower=0.0)
    ).to_numpy()
    observations["log_previous_cycle_count"] = np.log1p(
        pd.to_numeric(count_matched, errors="coerce").fillna(0.0)
    ).to_numpy()
    observations["has_failure_interval_trend"] = trend["has_failure_interval_trend"].to_numpy()
    observations["log_failure_interval_mean_days"] = trend["log_failure_interval_mean_days"].to_numpy()
    observations["failure_interval_trend_ratio"] = trend["failure_interval_trend_ratio"].to_numpy()
    observations["log_prior_corrective_60d"] = windowed["log_prior_corrective_60d"].to_numpy()
    observations["log_prior_corrective_90d"] = windowed["log_prior_corrective_90d"].to_numpy()
    return observations


def _at(cumulative: np.ndarray, position: np.ndarray) -> np.ndarray:
    return np.where(position > 0, cumulative[np.maximum(position - 1, 0)], 0)


def _first_occurrence(events: pd.DataFrame) -> np.ndarray:
    frame = events[["item_identifier_clean", "place_canonical_clean"]]
    known = frame["place_canonical_clean"].notna()
    return (known & ~frame.duplicated()).to_numpy()


def _count_before(sorted_times: dict, keys: pd.Series, at: np.ndarray) -> np.ndarray:
    result = np.zeros(len(at), dtype="int64")
    for key, rows in keys.groupby(keys, sort=False).indices.items():
        times = sorted_times.get(key)
        if times is not None:
            result[rows] = np.searchsorted(times, at[rows], side="left")
    return result


def attach_fleet(
    observations: pd.DataFrame, cycles: pd.DataFrame, failures: pd.DataFrame
) -> pd.DataFrame:
    observations = observations.reset_index(drop=True)
    at = observations["observation_on"].to_numpy("datetime64[ns]")
    window = at - np.timedelta64(config.FLEET_WINDOW_DAYS, "D")
    keys = observations["item_model_code_clean"].fillna(config.UNKNOWN_LABEL)

    def sort_by_model(frame: pd.DataFrame, column: str) -> dict:
        usable = frame.loc[frame[column].notna()]
        grouped = usable.groupby(
            usable["item_model_code_clean"].fillna(config.UNKNOWN_LABEL), sort=False
        )
        return {
            name: np.sort(group[column].to_numpy("datetime64[ns]"))
            for name, group in grouped
        }

    cohort = cycles.loc[cycles["is_initial_model_cohort"].fillna(False)]
    eligible_failures = failures.loc[failures["is_initial_model_cohort"].fillna(False)]

    failure_times = sort_by_model(eligible_failures, "failure_onset_on")
    installed = sort_by_model(cohort, "installed_on")
    ended = sort_by_model(cohort, "cycle_end_on")

    recent = _count_before(failure_times, keys, at) - _count_before(failure_times, keys, window)
    fleet = np.maximum(
        _count_before(installed, keys, at) - _count_before(ended, keys, at), 0
    )
    return _fleet_columns(observations, np.maximum(recent, 0), fleet)


def fleet_snapshot(
    cycles: pd.DataFrame, episodes: pd.DataFrame, at: pd.Timestamp
) -> pd.DataFrame:

    models = pd.Series(cycles["item_model_code_clean"].dropna().unique(), name="model")
    frame = pd.DataFrame({"item_model_code_clean": models.to_numpy()})
    frame["observation_on"] = pd.Timestamp(at)
    return attach_fleet(frame, cycles, episodes)[
        ["item_model_code_clean", *config.FLEET_FEATURES]
    ]


def attach_fleet_snapshot(
    observations: pd.DataFrame, snapshot: pd.DataFrame
) -> pd.DataFrame:
    observations = observations.reset_index(drop=True)
    lookup = snapshot.set_index("item_model_code_clean")
    model = observations["item_model_code_clean"]
    for column in config.FLEET_FEATURES:
        observations[column] = model.map(lookup[column]).fillna(0.0).astype(float)
    return observations


def _fleet_columns(
    observations: pd.DataFrame, recent: np.ndarray, fleet: np.ndarray
) -> pd.DataFrame:
    observations["log_model_failures_90d"] = np.log1p(recent)
    observations["model_failure_rate_90d"] = recent / np.maximum(fleet, 1)
    observations["log_model_fleet_size"] = np.log1p(fleet)
    return observations


ITEM_TYPE_DENSITY_WINDOWS = (90, 180)


def local_density(
    observations: pd.DataFrame, cycles: pd.DataFrame, failures: pd.DataFrame,
    group_column: str, window_days: float,
) -> tuple[np.ndarray, np.ndarray]:
    observations = observations.reset_index(drop=True)
    at = observations["observation_on"].to_numpy("datetime64[ns]")
    window = at - np.timedelta64(int(window_days), "D")
    keys = observations[group_column].fillna(config.UNKNOWN_LABEL)

    def sort_by_group(frame: pd.DataFrame, time_col: str) -> dict:
        usable = frame.loc[frame[time_col].notna() & frame[group_column].notna()]
        grouped = usable.groupby(
            usable[group_column].fillna(config.UNKNOWN_LABEL), sort=False
        )
        return {
            name: np.sort(group[time_col].to_numpy("datetime64[ns]"))
            for name, group in grouped
        }

    cohort = cycles.loc[cycles["is_initial_model_cohort"].fillna(False)]
    eligible_failures = failures.loc[failures["is_initial_model_cohort"].fillna(False)]

    failure_times = sort_by_group(eligible_failures, "failure_onset_on")
    installed = sort_by_group(cohort, "installed_on")
    ended = sort_by_group(cohort, "cycle_end_on")

    recent = np.maximum(
        _count_before(failure_times, keys, at) - _count_before(failure_times, keys, window), 0
    )
    fleet = np.maximum(
        _count_before(installed, keys, at) - _count_before(ended, keys, at), 0
    )
    return recent, fleet


def _item_type_density_columns(frame: pd.DataFrame, cycles_aug: pd.DataFrame, episodes_aug: pd.DataFrame) -> pd.DataFrame:
    for window in ITEM_TYPE_DENSITY_WINDOWS:
        recent, fleet = local_density(frame, cycles_aug, episodes_aug, "item_type_at_install", window)
        frame[f"log_item_type_failures_{window}d"] = np.log1p(recent)
        frame[f"item_type_failure_rate_{window}d"] = recent / np.maximum(fleet, 1)
    return frame


def attach_item_type_density(
    observations: pd.DataFrame, events: pd.DataFrame, cycles: pd.DataFrame, episodes: pd.DataFrame,
) -> pd.DataFrame:
    from partrisk.core import features_survival

    observations = features_survival.attach_install_context(observations, events)
    cycles_aug = features_survival.attach_install_context(cycles, events)
    episodes_aug = episodes.rename(columns={"item_type_clean": "item_type_at_install"})
    return _item_type_density_columns(observations, cycles_aug, episodes_aug)


def item_type_density_snapshot(
    cycles: pd.DataFrame, events: pd.DataFrame, episodes: pd.DataFrame, at: pd.Timestamp,
) -> pd.DataFrame:
    from partrisk.core import features_survival

    cycles_aug = features_survival.attach_install_context(cycles, events)
    episodes_aug = episodes.rename(columns={"item_type_clean": "item_type_at_install"})
    types = pd.Series(cycles_aug["item_type_at_install"].dropna().unique(), name="item_type_at_install")
    frame = pd.DataFrame({"item_type_at_install": types.to_numpy()})
    frame["observation_on"] = pd.Timestamp(at)
    frame = _item_type_density_columns(frame, cycles_aug, episodes_aug)
    density_columns = [c for c in frame.columns if c not in ("item_type_at_install", "observation_on")]
    return frame[["item_type_at_install", *density_columns]]


def attach_item_type_density_snapshot(
    observations: pd.DataFrame, events: pd.DataFrame, snapshot: pd.DataFrame,
) -> pd.DataFrame:
    from partrisk.core import features_survival

    observations = features_survival.attach_install_context(observations, events).reset_index(drop=True)
    lookup = snapshot.set_index("item_type_at_install")
    key = observations["item_type_at_install"]
    density_columns = [c for c in snapshot.columns if c != "item_type_at_install"]
    for column in density_columns:
        observations[column] = key.map(lookup[column]).fillna(0.0).astype(float)
    return observations


def training_observations(
    cycles: pd.DataFrame, horizon_days: int = config.TARGET_HORIZON_DAYS
) -> pd.DataFrame:
    cohort = cycles.loc[
        cycles["is_initial_model_cohort"].fillna(False)
        & (cycles["installed_on"] < cycles["cycle_end_on"])
    ].reset_index(drop=True)

    installed = cohort["installed_on"].to_numpy("datetime64[ns]")
    ends = cohort["cycle_end_on"].to_numpy("datetime64[ns]")
    step = np.timedelta64(config.OBSERVATION_STEP_DAYS, "D")

    span = ends - installed - np.timedelta64(1, "us")
    n_steps = (span // step).astype("int64") + 1

    row = np.repeat(np.arange(len(cohort)), n_steps)
    offset = np.arange(n_steps.sum()) - np.repeat(
        np.cumsum(n_steps) - n_steps, n_steps
    )
    observations = cohort.iloc[row].reset_index(drop=True)
    observations["observation_on"] = installed[row] + offset * step

    failure = observations["failure_onset_on"].to_numpy("datetime64[ns]")
    observed = observations["observation_on"].to_numpy("datetime64[ns]")
    horizon = np.timedelta64(horizon_days, "D")
    observations["target_failure"] = (failure > observed) & (
        failure <= observed + horizon
    )

    observations["is_eligible"] = observations["target_failure"] | (
        observations["is_recon_verified_negative_eligible"].fillna(False)
        & (
            observations["observation_on"]
            <= observations["last_confirmable_observation_on"]
        )
    )
    return _finalize_observations(observations)


def _still_installed(item_ids: pd.Series, events: pd.DataFrame) -> pd.Series:

    latest_status = (
        events.sort_values(["item_identifier_clean", "created_on", "journey_id"])
        .groupby("item_identifier_clean")["status_clean"].last()
    )
    return item_ids.map(latest_status).eq("INSTALLED").fillna(False)


def current_observations(cycles: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    active = cycles.loc[
        cycles["is_initial_model_cohort"].fillna(False)
        & cycles["cycle_end_reason"].eq("RIGHT_CENSORED_AT_DATA_END")
    ].copy()
    active = active.loc[_still_installed(active["item_identifier_clean"], events)]
    active["observation_on"] = active["dataset_max_event_on"]
    return _finalize_observations(active)


def _finalize_observations(observations: pd.DataFrame) -> pd.DataFrame:
    observations = observations.reset_index(drop=True)
    observations["days_since_installation"] = (
        observations["observation_on"].to_numpy("datetime64[ns]")
        - observations["installed_on"].to_numpy("datetime64[ns]")
    ) / _DAY
    return observations


def build_features(raw: pd.DataFrame, support: pd.Series) -> pd.DataFrame:
    features = pd.DataFrame(index=raw.index)

    model_code = raw["item_model_code_clean"]
    features["part_model_category"] = np.where(
        model_code.isna(),
        config.UNKNOWN_LABEL,
        np.where(
            pd.to_numeric(support, errors="coerce").fillna(0)
            < config.MIN_PART_MODEL_SUPPORT,
            config.LOW_SUPPORT_LABEL,
            model_code.astype("string").fillna(config.UNKNOWN_LABEL),
        ),
    )
    features["client_category"] = (
        raw["installed_client_clean"].fillna(config.UNKNOWN_LABEL).astype(str)
    )

    days_since_installation = pd.to_numeric(
        raw["days_since_installation"], errors="coerce"
    ).fillna(0.0)
    features["installation_age_band"] = _age_band(days_since_installation)
    features["log_days_since_installation"] = _log1p(days_since_installation)

    features["log_total_prior_events"] = _log1p(raw["total_prior_events"])
    features["log_prior_failure_count"] = _log1p(raw["prior_failure_count"])
    features["has_prior_failure"] = (
        pd.to_numeric(raw["prior_failure_count"], errors="coerce").fillna(0) > 0
    )
    features["log_prior_corrective_count"] = _log1p(raw["prior_corrective_count"])
    features["has_prior_corrective"] = (
        pd.to_numeric(raw["prior_corrective_count"], errors="coerce").fillna(0) > 0
    )
    features["log_days_since_last_corrective"] = _log1p(raw["days_since_last_corrective"])
    features["log_prior_distinct_places"] = _log1p(raw["prior_distinct_places"])

    features["log_prior_corrective_30d"] = _log1p(raw["prior_corrective_30d"])
    features["log_prior_failure_365d"] = _log1p(raw["prior_failure_365d"])
    features["log_prior_events_180d"] = _log1p(raw["prior_events_180d"])

    features["log_previous_cycle_lifetime_mean"] = _log1p(
        raw["previous_cycle_lifetime_mean"]
    )
    features["has_previous_cycle"] = raw["has_previous_cycle"].fillna(False).astype(bool)

    features["log_cumulative_prior_cycle_days"] = pd.to_numeric(
        raw["log_cumulative_prior_cycle_days"], errors="coerce"
    ).fillna(0.0)
    features["log_previous_cycle_count"] = pd.to_numeric(
        raw["log_previous_cycle_count"], errors="coerce"
    ).fillna(0.0)
    features["has_failure_interval_trend"] = raw["has_failure_interval_trend"].fillna(False).astype(bool)
    features["log_failure_interval_mean_days"] = pd.to_numeric(
        raw["log_failure_interval_mean_days"], errors="coerce"
    ).fillna(0.0)
    features["failure_interval_trend_ratio"] = pd.to_numeric(
        raw["failure_interval_trend_ratio"], errors="coerce"
    ).fillna(1.0)
    features["log_prior_corrective_60d"] = pd.to_numeric(
        raw["log_prior_corrective_60d"], errors="coerce"
    ).fillna(0.0)
    features["log_prior_corrective_90d"] = pd.to_numeric(
        raw["log_prior_corrective_90d"], errors="coerce"
    ).fillna(0.0)

    month = pd.to_datetime(raw["observation_on"]).dt.month
    features["month_sin"] = np.sin(2.0 * np.pi * (month - 1) / 12.0)
    features["month_cos"] = np.cos(2.0 * np.pi * (month - 1) / 12.0)

    for column in config.FLEET_FEATURES:
        features[column] = pd.to_numeric(raw[column], errors="coerce").fillna(0.0)

    for column in config.LOCAL_DENSITY_FEATURES:
        features[column] = pd.to_numeric(raw[column], errors="coerce").fillna(0.0)

    features[config.CATEGORICAL_FEATURES] = features[config.CATEGORICAL_FEATURES].astype(str)
    numeric = (
        config.NUMERIC_FEATURES + config.FLEET_FEATURES
        + config.DEGRADATION_FEATURES + config.LOCAL_DENSITY_FEATURES
    )
    features[numeric] = features[numeric].astype(float)
    return features[config.FEATURE_COLUMNS]


def project_features(raw: pd.DataFrame, support: pd.Series, steps_ahead: int) -> pd.DataFrame:
    if steps_ahead == 0:
        return build_features(raw, support)

    elapsed_days = steps_ahead * config.OBSERVATION_STEP_DAYS
    shifted = raw.copy()
    shifted["days_since_installation"] = (
        pd.to_numeric(raw["days_since_installation"], errors="coerce").fillna(0.0)
        + elapsed_days
    )
    shifted["days_since_last_corrective"] = (
        pd.to_numeric(raw["days_since_last_corrective"], errors="coerce") + elapsed_days
    )
    shifted["observation_on"] = pd.to_datetime(raw["observation_on"]) + pd.to_timedelta(
        elapsed_days, unit="D"
    )
    return build_features(shifted, support)


_TERMINAL_STATUS = set(config.FAILURE_OUTCOME_STATUS) | {config.REPAIR_COMPLETED_STATUS}


def _first_after(times: np.ndarray, journeys: np.ndarray, moment, journey_id: int) -> int:
    low = int(np.searchsorted(times, moment, side="left"))
    high = int(np.searchsorted(times, moment, side="right"))
    within = low + int(np.searchsorted(journeys[low:high], journey_id, side="right"))
    return within if within < high else high


def resolve_outcomes(
    episodes: pd.DataFrame, events: pd.DataFrame, cycles: pd.DataFrame, data_end: pd.Timestamp
) -> pd.DataFrame:
    episodes = episodes.reset_index(drop=True).copy()
    episodes["failure_onset_on"] = pd.to_datetime(episodes["failure_onset_on"])

    events = events.sort_values(
        ["item_identifier_clean", "created_on", "journey_id"], kind="stable"
    ).reset_index(drop=True)
    event_times = events["created_on"].to_numpy("datetime64[ns]")
    event_journeys = events["journey_id"].to_numpy("int64")
    status = events["status_clean"].fillna("").to_numpy(dtype=object)
    is_failure = events["is_failure_onset"].fillna(False).to_numpy(dtype=bool)
    is_repaired = status == config.REPAIR_COMPLETED_STATUS
    is_installed = status == "INSTALLED"
    is_terminal = np.array([s in _TERMINAL_STATUS for s in status], dtype=bool)
    rows_by_item = events.groupby("item_identifier_clean", sort=False).indices

    total = len(episodes)
    outcome = np.full(total, None, dtype=object)
    reinstalled = np.zeros(total, dtype=bool)
    prior_repaired = np.zeros(total, dtype="int64")
    prior_failures = np.zeros(total, dtype="int64")
    first_seen = np.full(total, np.datetime64("NaT"), dtype="datetime64[ns]")

    onsets = episodes["failure_onset_on"].to_numpy("datetime64[ns]")
    onset_journeys = episodes["onset_journey_id"].to_numpy("int64")

    for position, item in enumerate(episodes["item_identifier_clean"]):
        slot = rows_by_item.get(item)
        if slot is None:
            continue
        times, journeys = event_times[slot], event_journeys[slot]
        moment, journey_id = onsets[position], onset_journeys[position]

        seen = _first_after(times, journeys, moment, journey_id)
        prior_repaired[position] = int(is_repaired[slot][:seen].sum())
        prior_failures[position] = int(is_failure[slot][:seen].sum())
        first_seen[position] = times[0]

        after_installed = np.flatnonzero(is_installed[slot][seen:])
        after_failure = np.flatnonzero(is_failure[slot][seen:])
        if after_installed.size:
            reinstalled[position] = True

        limits = [times[seen + a[0]] for a in (after_installed, after_failure) if a.size]
        boundary = min(limits) if limits else None

        start = int(np.searchsorted(times, moment, side="left"))
        candidates = np.flatnonzero(is_terminal[slot][start:])
        for offset in candidates:
            index = start + offset
            if boundary is not None and times[index] > boundary:
                break
            outcome[position] = status[slot][index]
            break

    episodes["outcome"] = outcome
    episodes["was_reinstalled"] = reinstalled
    episodes["prior_repaired_count"] = prior_repaired
    episodes["prior_failure_count"] = prior_failures
    episodes["age_total_days"] = (onsets - first_seen) / _DAY

    installs = (
        cycles[["item_identifier_clean", "installed_on"]]
        .assign(installed_on=lambda f: pd.to_datetime(f["installed_on"]))
        .sort_values("installed_on")
    )
    episodes = pd.merge_asof(
        episodes.sort_values("failure_onset_on"),
        installs,
        left_on="failure_onset_on",
        right_on="installed_on",
        by="item_identifier_clean",
        direction="backward",
    ).reset_index(drop=True)
    episodes["cycle_age_days"] = (
        episodes["failure_onset_on"] - episodes["installed_on"]
    ).dt.total_seconds() / 86400.0

    is_scrap = episodes["outcome"].isin(config.SCRAP_STATUS)
    survived = episodes["outcome"].eq(config.REPAIR_COMPLETED_STATUS) | episodes["was_reinstalled"]
    episodes["is_scrap"] = is_scrap.astype(int)
    past_embargo = episodes["failure_onset_on"] <= (
        pd.Timestamp(data_end) - pd.Timedelta(days=config.SCRAP_EMBARGO_DAYS)
    )
    episodes["is_labeled"] = (is_scrap | survived) & past_embargo
    return episodes


def current_state(
    events: pd.DataFrame, cycles: pd.DataFrame, data_end: pd.Timestamp
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    moment = pd.Timestamp(data_end)
    times = pd.to_datetime(events["created_on"])
    seen = times <= moment
    if not seen.any():
        return pd.DataFrame()

    status = events["status_clean"].fillna("")
    item_type = events.loc[seen, "item_type_clean"].dropna()
    installed = pd.to_datetime(cycles["installed_on"]) if len(cycles) else pd.Series(dtype="datetime64[ns]")
    started = installed[installed <= moment]

    return pd.DataFrame([{
        "item_identifier_clean": events["item_identifier_clean"].iloc[0],
        "failure_onset_on": moment,
        "item_type_clean": item_type.iloc[-1] if len(item_type) else None,
        "age_total_days": (moment - times[seen].min()).total_seconds() / 86400.0,
        "cycle_age_days": (
            (moment - started.max()).total_seconds() / 86400.0 if len(started) else np.nan
        ),
        "prior_repaired_count": int((seen & status.eq(config.REPAIR_COMPLETED_STATUS)).sum()),
        "prior_failure_count": int(
            (seen & events["is_failure_onset"].fillna(False)).sum()
        ),
    }])


def known_item_types(labeled: pd.DataFrame) -> list[str]:
    counts = labeled["item_type_clean"].value_counts()
    return sorted(counts[counts >= config.SCRAP_MIN_TYPE_SUPPORT].index)


def build_scrap_features(episodes: pd.DataFrame, known_types: list[str]) -> pd.DataFrame:
    repaired = pd.to_numeric(episodes["prior_repaired_count"], errors="coerce").fillna(0)
    failures = pd.to_numeric(episodes["prior_failure_count"], errors="coerce").fillna(0)

    features = pd.DataFrame(index=episodes.index)
    features["item_type_category"] = (
        episodes["item_type_clean"]
        .where(episodes["item_type_clean"].isin(known_types), "LOW_SUPPORT")
        .fillna(config.UNKNOWN_LABEL)
        .astype(str)
    )
    features["log_age_total"] = _log1p(episodes["age_total_days"])
    features["log_cycle_age"] = _log1p(episodes["cycle_age_days"])
    features["log_prior_repaired_count"] = np.log1p(repaired)
    features["has_prior_repair"] = (repaired > 0).astype(float)
    features["log_prior_failure_count"] = np.log1p(failures)
    features["is_first_failure_ever"] = (failures <= 1).astype(float)

    features[config.SCRAP_CATEGORICAL_FEATURES] = features[
        config.SCRAP_CATEGORICAL_FEATURES
    ].astype(str)
    features[config.SCRAP_NUMERIC_FEATURES] = features[
        config.SCRAP_NUMERIC_FEATURES
    ].astype(float)
    return features[config.SCRAP_FEATURE_COLUMNS]
