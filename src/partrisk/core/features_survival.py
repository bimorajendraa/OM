from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import OneHotEncoder

from partrisk.core import config
from partrisk.core import features as feature_builder

UNKNOWN_LABEL = "UNKNOWN"
LOW_SUPPORT_LABEL = "LOW_SUPPORT"


def apply_threshold(
    values: pd.Series, support: pd.Series, threshold: int,
    *, low_label: str = LOW_SUPPORT_LABEL, unknown_label: str = UNKNOWN_LABEL,
) -> pd.Series:
    support_numeric = pd.to_numeric(support, errors="coerce").fillna(0)
    return pd.Series(
        np.where(
            values.isna(),
            unknown_label,
            np.where(support_numeric < threshold, low_label, values.astype(str)),
        ),
        index=values.index,
    )


def attach_install_context(observations: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    installed_events = (
        events.loc[
            events["status_clean"].eq("INSTALLED"),
            ["item_identifier_clean", "created_on", "item_type_clean"],
        ]
        .drop_duplicates(subset=["item_identifier_clean", "created_on"], keep="first")
    )
    merged = observations.merge(
        installed_events,
        left_on=["item_identifier_clean", "installed_on"],
        right_on=["item_identifier_clean", "created_on"],
        how="left",
    )
    merged["item_type_at_install"] = merged["item_type_clean"].fillna(UNKNOWN_LABEL).astype(str)
    return merged.drop(columns=["created_on", "item_type_clean"])


def attach_terminal_context(observations: pd.DataFrame, terminal_raw: pd.DataFrame) -> pd.DataFrame:
    safe = terminal_raw.loc[terminal_raw["parent_link_quality_status"].eq("VALID_POINT_IN_TIME_RELATION")].copy()
    safe = safe.drop_duplicates(subset=["item_identifier_clean", "installed_on"], keep="first")

    merged = observations.merge(
        safe[["item_identifier_clean", "installed_on", "terminal_type_clean", "terminal_model_code_clean"]],
        on=["item_identifier_clean", "installed_on"], how="left",
    )
    merged["terminal_type_context"] = merged["terminal_type_clean"].fillna(UNKNOWN_LABEL).astype(str)
    merged["terminal_model_context"] = merged["terminal_model_code_clean"].fillna(UNKNOWN_LABEL).astype(str)
    return merged.drop(columns=["terminal_type_clean", "terminal_model_code_clean"])


def cumulative_cycle_age(cycles: pd.DataFrame) -> pd.DataFrame:
    frame = cycles.reset_index(drop=True).copy()
    frame["_sequence"] = frame["installation_cycle_id"].str.rsplit(":", n=1).str[-1].astype(int)
    frame = frame.sort_values(["item_identifier_clean", "_sequence"], kind="stable")

    duration_days = (frame["cycle_end_on"] - frame["installed_on"]) / np.timedelta64(1, "D")
    frame["_duration"] = duration_days.clip(lower=0.0)

    grouped = frame.groupby("item_identifier_clean", sort=False)["_duration"]

    cumulative = grouped.cumsum()
    frame["cumulative_prior_cycle_days"] = (
        cumulative.groupby(frame["item_identifier_clean"], sort=False).shift(1).fillna(0.0)
    )
    frame["previous_cycle_count"] = frame.groupby("item_identifier_clean", sort=False).cumcount()

    return frame[["installation_cycle_id", "cumulative_prior_cycle_days", "previous_cycle_count"]].sort_index()


def corrective_degradation_trend(landmarks: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    landmarks = landmarks.reset_index(drop=True)
    n = len(landmarks)
    mean_gap = np.zeros(n)
    last_gap = np.zeros(n)
    trend_ratio = np.zeros(n)
    has_trend = np.zeros(n, dtype=bool)

    failures = events.loc[events["is_failure_onset"].fillna(False)].sort_values(
        ["item_identifier_clean", "created_on"], kind="stable"
    )
    failure_times_by_item = {
        item: sub["created_on"].to_numpy("datetime64[ns]")
        for item, sub in failures.groupby("item_identifier_clean", sort=False)
    }

    at = landmarks["observation_on"].to_numpy("datetime64[ns]")
    day = np.timedelta64(1, "D")

    rows_by_item = landmarks.groupby("item_identifier_clean", sort=False).indices
    for item, rows in rows_by_item.items():
        times = failure_times_by_item.get(item)
        if times is None or len(times) < 3:
            continue
        gaps = (times[1:] - times[:-1]) / day
        cum_mean = np.cumsum(gaps) / np.arange(1, len(gaps) + 1)

        rows_arr = rows.to_numpy() if hasattr(rows, "to_numpy") else np.asarray(rows)
        pos = np.searchsorted(times, at[rows_arr], side="left")
        eligible = pos >= 3
        idx = np.clip(pos - 2, 0, len(gaps) - 1)
        mean_gap[rows_arr[eligible]] = cum_mean[idx[eligible]]
        last_gap[rows_arr[eligible]] = gaps[idx[eligible]]
        has_trend[rows_arr[eligible]] = True

    valid_mean = has_trend & (mean_gap > 0)
    trend_ratio[valid_mean] = last_gap[valid_mean] / mean_gap[valid_mean]

    out = pd.DataFrame(index=landmarks.index)
    out["has_failure_interval_trend"] = has_trend
    out["log_failure_interval_mean_days"] = np.log1p(np.clip(mean_gap, 0, None))
    out["log_failure_interval_last_days"] = np.log1p(np.clip(last_gap, 0, None))
    out["failure_interval_trend_ratio"] = np.where(valid_mean, np.clip(trend_ratio, 0, 10), 1.0)
    return out


def windowed_corrective_extra(landmarks: pd.DataFrame, events: pd.DataFrame, windows=(60, 90)) -> pd.DataFrame:
    landmarks = landmarks.reset_index(drop=True)
    n = len(landmarks)
    out = pd.DataFrame(index=landmarks.index)
    for w in windows:
        out[f"prior_corrective_{w}d"] = np.zeros(n, dtype="int64")

    corrective = events.loc[events["wo_type_clean"].eq("CORRECTIVE")].sort_values(
        ["item_identifier_clean", "created_on"], kind="stable"
    )
    times_by_item = {
        item: sub["created_on"].to_numpy("datetime64[ns]")
        for item, sub in corrective.groupby("item_identifier_clean", sort=False)
    }

    at = landmarks["observation_on"].to_numpy("datetime64[ns]")
    rows_by_item = landmarks.groupby("item_identifier_clean", sort=False).indices

    for item, rows in rows_by_item.items():
        times = times_by_item.get(item)
        if times is None or not len(times):
            continue
        rows_arr = rows.to_numpy() if hasattr(rows, "to_numpy") else np.asarray(rows)
        query = at[rows_arr]
        seen = np.searchsorted(times, query, side="right")
        for w in windows:
            window_start = query - np.timedelta64(w, "D")
            seen_window = np.searchsorted(times, window_start, side="right")
            out.loc[rows_arr, f"prior_corrective_{w}d"] = seen - seen_window

    for w in windows:
        out[f"log_prior_corrective_{w}d"] = np.log1p(out[f"prior_corrective_{w}d"])
    return out


_DAY = np.timedelta64(1, "D")
ANCHOR_BASE_AGES_DAYS = (90.0, 180.0, 365.0)
ANCHOR_STEP_DAYS = 365.0
MAX_ANCHORS_PER_LIFECYCLE = 8
MAX_ORGANIC_PER_LIFECYCLE = 8


def _anchor_ages(max_age_days: float, max_anchors: int = MAX_ANCHORS_PER_LIFECYCLE) -> list[float]:
    ages = list(ANCHOR_BASE_AGES_DAYS)
    while len(ages) < max_anchors:
        ages.append(ages[-1] + ANCHOR_STEP_DAYS)
    return [a for a in ages if a < max_age_days]


def build_landmarks(outcome: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    eligible = outcome.loc[outcome["eligible"]].reset_index(drop=True)

    events_sorted = events.sort_values(["item_identifier_clean", "created_on"], kind="stable")
    event_times_by_item = {
        item: sub["created_on"].to_numpy("datetime64[ns]")
        for item, sub in events_sorted.groupby("item_identifier_clean", sort=False)
    }

    installed = eligible["installed_on"].to_numpy("datetime64[ns]")
    duration = eligible["duration_days"].to_numpy(dtype=float)
    items = eligible["item_identifier_clean"].to_numpy()

    rows_ages: list[np.ndarray] = []
    rows_source: list[np.ndarray] = []
    rows_index: list[np.ndarray] = []

    for i in range(len(eligible)):
        max_age = duration[i]
        ages = [0.0]
        sources = ["INSTALL"]

        item_times = event_times_by_item.get(items[i])
        if item_times is not None and len(item_times):
            install_i = installed[i]
            end_i = install_i + np.timedelta64(int(round(max_age)), "D")
            in_window = item_times[(item_times > install_i) & (item_times < end_i)]
            if len(in_window):
                organic_ages = (in_window - install_i) / _DAY
                organic_ages = np.unique(np.round(organic_ages))
                organic_ages = organic_ages[(organic_ages > 0) & (organic_ages < max_age)]
                if len(organic_ages) > MAX_ORGANIC_PER_LIFECYCLE:
                    organic_ages = organic_ages[-MAX_ORGANIC_PER_LIFECYCLE:]
                ages.extend(organic_ages.tolist())
                sources.extend(["ORGANIC_EVENT"] * len(organic_ages))

        for age in _anchor_ages(max_age):
            ages.append(age)
            sources.append("ANCHOR")

        ages_arr = np.round(np.asarray(ages, dtype=float))
        priority = {"INSTALL": 0, "ORGANIC_EVENT": 1, "ANCHOR": 2}
        order = sorted(range(len(ages_arr)), key=lambda k: (ages_arr[k], priority[sources[k]]))
        seen: set[float] = set()
        keep_idx: list[int] = []
        for k in order:
            if ages_arr[k] not in seen:
                seen.add(ages_arr[k])
                keep_idx.append(k)

        final_ages = ages_arr[keep_idx]
        final_sources = np.asarray(sources)[keep_idx]
        valid = final_ages < max_age
        rows_ages.append(final_ages[valid])
        rows_source.append(final_sources[valid])
        rows_index.append(np.full(valid.sum(), i))

    landmark_age = np.concatenate(rows_ages)
    landmark_source = np.concatenate(rows_source)
    source_row = np.concatenate(rows_index)

    landmarks = eligible.iloc[source_row].reset_index(drop=True)
    landmarks["landmark_age_days"] = landmark_age
    landmarks["landmark_source"] = landmark_source
    landmarks["observation_on"] = (
        landmarks["installed_on"].to_numpy("datetime64[ns]")
        + landmark_age.astype("timedelta64[D]")
    )
    landmarks["duration_days"] = landmarks["duration_days"].to_numpy(dtype=float) - landmark_age

    return landmarks


TRAIN, VALIDATION, TEST, EXCLUDED_TOO_OLD = "TRAIN", "VALIDATION", "TEST", "EXCLUDED_TOO_OLD"


def lifecycle_split_bounds(data_end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    test_start = pd.Timestamp(year=data_end.year, month=1, day=1)
    validation_start = test_start - pd.DateOffset(years=1)
    return validation_start, test_start


def assign_lifecycle_split(installed_on: pd.Series, data_end: pd.Timestamp) -> pd.Series:
    validation_start, test_start = lifecycle_split_bounds(data_end)
    installed_on = pd.to_datetime(installed_on)
    split = pd.Series(EXCLUDED_TOO_OLD, index=installed_on.index)
    split[installed_on >= pd.Timestamp(config.MIN_OBSERVATION_DATE)] = TRAIN
    split[installed_on >= validation_start] = VALIDATION
    split[installed_on >= test_start] = TEST
    return split


_REQUIRED_COLUMNS = [
    "installation_cycle_id", "item_identifier_clean", "installed_on",
    "item_model_code_clean", "installed_client_clean", "failure_onset_on",
    "cycle_end_on", "cycle_end_reason", "is_recon_verified_negative_eligible",
    "is_initial_model_cohort", "previous_cycle_lifetime_mean", "has_previous_cycle",
]


def cohort_cycles(cycles: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in _REQUIRED_COLUMNS if c not in cycles.columns]
    if missing:
        raise ValueError(f"Kolom hilang dari data_reader.get_cycles(): {missing}")
    return cycles.loc[
        cycles["is_initial_model_cohort"].fillna(False)
        & (cycles["installed_on"] < cycles["cycle_end_on"])
    ].reset_index(drop=True)


def assign_lifecycle_outcome(cohort: pd.DataFrame, data_end: pd.Timestamp) -> pd.DataFrame:
    outcome = cohort.copy()
    outcome["split"] = assign_lifecycle_split(outcome["installed_on"], data_end)
    validation_start, test_start = lifecycle_split_bounds(data_end)
    cutoff_by_split = {
        TRAIN: validation_start,
        VALIDATION: test_start,
        TEST: pd.Timestamp(data_end),
    }
    outcome["cutoff_on"] = outcome["split"].map(cutoff_by_split)

    installed = outcome["installed_on"].to_numpy("datetime64[ns]")
    cutoff = pd.to_datetime(outcome["cutoff_on"]).to_numpy("datetime64[ns]")
    failure = outcome["failure_onset_on"].to_numpy("datetime64[ns]")
    cycle_end = outcome["cycle_end_on"].to_numpy("datetime64[ns]")
    day = np.timedelta64(1, "D")

    has_cutoff = ~pd.isna(outcome["cutoff_on"]).to_numpy()
    has_failure_before_cutoff = ~pd.isna(outcome["failure_onset_on"]).to_numpy() & has_cutoff & (failure <= cutoff)
    still_running_at_cutoff = has_cutoff & (cycle_end > cutoff)
    verified_censored_at_end = (
        has_cutoff
        & (
            outcome["cycle_end_reason"].isin(["RETURNED", "DISMANTLED"]).to_numpy()
            | (
                outcome["cycle_end_reason"].eq("RIGHT_CENSORED_AT_DATA_END").to_numpy()
                & outcome["is_recon_verified_negative_eligible"].fillna(False).to_numpy()
            )
        )
        & (cycle_end <= cutoff)
    )
    positive_span = has_cutoff & (installed < cutoff)

    eligible = positive_span & (has_failure_before_cutoff | still_running_at_cutoff | verified_censored_at_end)

    duration_days = np.where(
        has_failure_before_cutoff,
        (failure - installed) / day,
        np.where(
            verified_censored_at_end,
            (cycle_end - installed) / day,
            (cutoff - installed) / day,
        ),
    )

    outcome["duration_days"] = np.maximum(np.round(duration_days), 1.0)
    outcome["event_observed"] = has_failure_before_cutoff.astype(int)
    outcome["eligible"] = eligible
    return outcome


NONE_FIRST_CYCLE = "NONE_FIRST_CYCLE"


def audit_previous_cycle_features(cycles: pd.DataFrame) -> pd.DataFrame:
    frame = cycles.reset_index(drop=True).copy()
    frame["_sequence"] = (
        frame["installation_cycle_id"].str.rsplit(":", n=1).str[-1].astype(int)
    )
    frame = frame.sort_values(["item_identifier_clean", "_sequence"], kind="stable")

    duration_days = (frame["cycle_end_on"] - frame["installed_on"]) / np.timedelta64(1, "D")
    frame["_failure_duration"] = np.where(frame["cycle_end_reason"].eq("FAILURE"), duration_days, np.nan)

    frame["_shifted_failure_duration"] = (
        frame.groupby("item_identifier_clean", sort=False)["_failure_duration"].shift(1)
    )
    frame["previous_cycle_confirmed_failure_lifetime_mean"] = (
        frame.groupby("item_identifier_clean", sort=False)["_shifted_failure_duration"]
        .expanding().mean().reset_index(level=0, drop=True)
    )
    frame["last_confirmed_failure_lifetime"] = (
        frame.groupby("item_identifier_clean", sort=False)["_shifted_failure_duration"].ffill()
    )
    frame["previous_cycle_end_reason"] = (
        frame.groupby("item_identifier_clean", sort=False)["cycle_end_reason"]
        .shift(1).fillna(NONE_FIRST_CYCLE)
    )

    frame = frame.drop(columns=["_sequence", "_failure_duration", "_shifted_failure_duration"])
    return frame.sort_index()


def transform_for_model(observations: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=observations.index)
    for column in ("previous_cycle_confirmed_failure_lifetime_mean", "last_confirmed_failure_lifetime"):
        values = pd.to_numeric(observations[column], errors="coerce")
        out[f"log_{column}"] = np.log1p(values.fillna(0.0).clip(lower=0.0))
        out[f"has_{column}"] = values.notna()
    return out


CATEGORICAL_FEATURES = [
    "part_model_category",
    "client_category",
    "installation_age_band",
    "item_type_at_install_grouped",
    "terminal_type_grouped",
]
FINAL_TERMINAL_THRESHOLD = 200
_DROPPED_PREVIOUS_CYCLE = ["log_previous_cycle_lifetime_mean", "has_previous_cycle"]
_CONFIRMED_FAILURE_COLUMNS = [
    "log_previous_cycle_confirmed_failure_lifetime_mean",
    "has_previous_cycle_confirmed_failure_lifetime_mean",
]
DYNAMIC_EXTRA_NUMERIC_COLUMNS = [
    "log_failure_interval_mean_days", "log_failure_interval_last_days",
    "failure_interval_trend_ratio", "has_failure_interval_trend",
    "log_cumulative_prior_cycle_days", "log_physical_age_now", "previous_cycle_count",
    "prior_corrective_60d", "log_prior_corrective_60d", "prior_corrective_90d", "log_prior_corrective_90d",
]
NUMERIC_FEATURES = (
    [c for c in config.NUMERIC_FEATURES if c not in _DROPPED_PREVIOUS_CYCLE]
    + _CONFIRMED_FAILURE_COLUMNS + DYNAMIC_EXTRA_NUMERIC_COLUMNS
)
FLEET_FEATURES = list(config.FLEET_FEATURES)
FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES + FLEET_FEATURES

FINAL_CATEGORY_THRESHOLDS = {"item_model_code_clean": 200, "item_type_at_install": 300}


def point_in_time_support(
    landmarks: pd.DataFrame, baseline_installs: pd.DataFrame, group_column: str
) -> pd.Series:

    keys = landmarks[group_column].fillna(UNKNOWN_LABEL).astype(str)
    base_keys = baseline_installs[group_column].fillna(UNKNOWN_LABEL).astype(str)
    base_installed = baseline_installs["installed_on"].to_numpy("datetime64[ns]")
    at = landmarks["observation_on"].to_numpy("datetime64[ns]")

    result = np.zeros(len(landmarks), dtype="int64")
    grouped_base = pd.Series(base_installed, index=base_keys.to_numpy()).groupby(level=0)
    for key, rows in keys.groupby(keys, sort=False).indices.items():
        if key not in grouped_base.groups:
            continue
        times_sorted = np.sort(grouped_base.get_group(key).to_numpy())
        result[rows] = np.searchsorted(times_sorted, at[rows], side="right")
    return pd.Series(result, index=landmarks.index)


def attach_dynamic_extra(landmarks: pd.DataFrame, cycles: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    cum = cumulative_cycle_age(cycles)
    landmarks = landmarks.merge(cum, on="installation_cycle_id", how="left")
    physical_age_now = landmarks["cumulative_prior_cycle_days"].to_numpy() + landmarks["landmark_age_days"].to_numpy()
    landmarks["log_cumulative_prior_cycle_days"] = np.log1p(landmarks["cumulative_prior_cycle_days"].to_numpy())
    landmarks["log_physical_age_now"] = np.log1p(np.clip(physical_age_now, 0, None))
    landmarks["previous_cycle_count"] = landmarks["previous_cycle_count"].astype(float)

    trend = corrective_degradation_trend(landmarks, events)
    windowed = windowed_corrective_extra(landmarks, events)
    return pd.concat([landmarks.reset_index(drop=True), trend, windowed], axis=1)


def attach_terminal_extra(landmarks: pd.DataFrame, terminal_raw: pd.DataFrame) -> pd.DataFrame:
    return attach_terminal_context(landmarks, terminal_raw)


def compute_features(
    landmarks: pd.DataFrame, support: pd.Series, item_type_support: pd.Series, terminal_support: pd.Series
) -> pd.DataFrame:

    full = feature_builder.build_features(landmarks, support)

    result = pd.DataFrame(index=landmarks.index)
    result["part_model_category"] = apply_threshold(
        landmarks["item_model_code_clean"], support, FINAL_CATEGORY_THRESHOLDS["item_model_code_clean"]
    ).to_numpy()
    result["client_category"] = full["client_category"].to_numpy()
    result["installation_age_band"] = full["installation_age_band"].to_numpy()
    result["item_type_at_install_grouped"] = apply_threshold(
        landmarks["item_type_at_install"], item_type_support, FINAL_CATEGORY_THRESHOLDS["item_type_at_install"]
    ).to_numpy()
    result["terminal_type_grouped"] = apply_threshold(
        landmarks["terminal_type_context"], terminal_support, FINAL_TERMINAL_THRESHOLD
    ).to_numpy()

    for column in [c for c in config.NUMERIC_FEATURES if c not in _DROPPED_PREVIOUS_CYCLE] + FLEET_FEATURES:
        result[column] = full[column].to_numpy()
    for column in _CONFIRMED_FAILURE_COLUMNS:
        result[column] = landmarks[column].to_numpy()
    for column in DYNAMIC_EXTRA_NUMERIC_COLUMNS:
        result[column] = landmarks[column].to_numpy()

    return result[FEATURE_COLUMNS].reset_index(drop=True)


def fit_encoder(train_features: pd.DataFrame, categorical_columns: list[str] | None = None) -> OneHotEncoder:
    columns = categorical_columns if categorical_columns is not None else CATEGORICAL_FEATURES
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    encoder.fit(train_features[columns])
    encoder.feature_names_used_ = list(columns)
    return encoder


def encode(
    features: pd.DataFrame, encoder: OneHotEncoder, numeric_columns: list[str] | None = None
) -> pd.DataFrame:
    columns = list(getattr(encoder, "feature_names_used_", CATEGORICAL_FEATURES))
    dummy_values = encoder.transform(features[columns])
    dummy_columns = encoder.get_feature_names_out(columns)
    dummies = pd.DataFrame(dummy_values, columns=dummy_columns, index=features.index)
    numeric = numeric_columns if numeric_columns is not None else NUMERIC_FEATURES + FLEET_FEATURES
    numeric_frame = features[numeric].astype(float)
    return pd.concat([numeric_frame, dummies], axis=1)
