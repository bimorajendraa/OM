from __future__ import annotations

import numpy as np
from sksurv.ensemble import RandomSurvivalForest
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.metrics import (
    brier_score,
    concordance_index_censored,
    concordance_index_ipcw,
    cumulative_dynamic_auc,
    integrated_brier_score,
)
from sksurv.util import Surv


def survival_curve_arrays(fitted_model, features) -> tuple[np.ndarray, np.ndarray]:
    step_functions = fitted_model.predict_survival_function(features)
    times = np.asarray(step_functions[0].x, dtype=float)
    curves = np.vstack([np.asarray(fn.y, dtype=float) for fn in step_functions])
    return times, curves


def eval_survival_at(times: np.ndarray, curve: np.ndarray, t: float) -> float:
    if t <= 0 or t <= times[0]:
        return 1.0
    idx = int(np.searchsorted(times, t, side="right")) - 1
    idx = min(max(idx, 0), len(curve) - 1)
    return float(curve[idx])


def conditional_risk(times: np.ndarray, curve: np.ndarray, age_days: float, horizon_days: float) -> float:
    s_age = eval_survival_at(times, curve, age_days)
    if s_age <= 1e-9:
        return 1.0
    s_future = eval_survival_at(times, curve, age_days + horizon_days)
    return float(np.clip(1.0 - s_future / s_age, 0.0, 1.0))


def step_eval_matrix(times: np.ndarray, curves: np.ndarray, query_times: list[float]) -> np.ndarray:
    query_times = np.asarray(query_times, dtype=float)
    result = np.empty((curves.shape[0], len(query_times)))
    for j, t in enumerate(query_times):
        if t <= 0 or t <= times[0]:
            result[:, j] = 1.0
            continue
        idx = int(np.searchsorted(times, t, side="right")) - 1
        idx = min(max(idx, 0), curves.shape[1] - 1)
        result[:, j] = curves[:, idx]
    return result


def calibrate_curve(times: np.ndarray, curve_values: np.ndarray, calibrators: dict) -> np.ndarray:
    horizons = sorted(calibrators)
    n_rows, n_grid = curve_values.shape
    raw_risk = 1.0 - curve_values
    calibrated_risk = np.full_like(raw_risk, np.nan)

    mask = times <= horizons[0]
    if mask.any():
        calibrated_risk[:, mask] = calibrators[horizons[0]].predict(raw_risk[:, mask].ravel()).reshape(n_rows, mask.sum())
    mask = times > horizons[-1]
    if mask.any():
        calibrated_risk[:, mask] = calibrators[horizons[-1]].predict(raw_risk[:, mask].ravel()).reshape(n_rows, mask.sum())

    for h_lo, h_hi in zip(horizons[:-1], horizons[1:]):
        mask = (times > h_lo) & (times <= h_hi)
        if not mask.any():
            continue
        t_sub = times[mask]
        weight = (t_sub - h_lo) / (h_hi - h_lo)
        sub_raw = raw_risk[:, mask]
        r_lo = calibrators[h_lo].predict(sub_raw.ravel()).reshape(n_rows, mask.sum())
        r_hi = calibrators[h_hi].predict(sub_raw.ravel()).reshape(n_rows, mask.sum())
        calibrated_risk[:, mask] = (1 - weight)[None, :] * r_lo + weight[None, :] * r_hi

    assert not np.isnan(calibrated_risk).any(), "calibrate_curve(): ada titik grid yang tidak tercakup region manapun"
    calibrated_risk = np.maximum.accumulate(calibrated_risk, axis=1)
    return 1.0 - calibrated_risk


def survival_time_at_threshold(times: np.ndarray, curve: np.ndarray, threshold: float) -> float | None:
    below = np.where(curve <= threshold)[0]
    if len(below) == 0:
        return None
    return float(times[int(below[0])])


def median_survival_time(times: np.ndarray, curve: np.ndarray) -> float | None:
    return survival_time_at_threshold(times, curve, 0.5)


def mae_median_days(
    times: np.ndarray, calibrated_curve: np.ndarray, duration: np.ndarray, event: np.ndarray
) -> dict:
    """MAE median (kalibrasi) vs kejadian nyata, hanya baris event_observed=True.

    `calibrated_curve` harus hasil `calibrate_curve()` pada populasi TEST
    (bukan VALIDATION) karena calibrator-nya dilatih di VALIDATION - mengukur
    di VALIDATION akan bocor (calibrator dites di data yang sama dengan
    tempat ia dilatih). Lihat E-40 (docs/EXPERIMENTS.md).
    """
    event = np.asarray(event, dtype=bool)
    duration = np.asarray(duration, dtype=float)
    medians = np.array(
        [median_survival_time(times, calibrated_curve[i]) for i in range(calibrated_curve.shape[0])]
    )

    has_median = np.array([m is not None for m in medians])
    usable = event & has_median
    n_usable = int(usable.sum())
    if n_usable == 0:
        return {
            "n_event_observed": int(event.sum()), "n_usable": 0,
            "mae_days": None, "bias_days": None,
        }

    predicted = medians[usable].astype(float)
    actual = duration[usable]
    errors = predicted - actual
    return {
        "n_event_observed": int(event.sum()),
        "n_usable": n_usable,
        "mae_days": float(np.mean(np.abs(errors))),
        "bias_days": float(np.mean(errors)),
    }


HORIZONS_DAYS = [30, 60, 90, 120]


def _usable_horizons(y_train, y_eval, horizons: list[int] = HORIZONS_DAYS) -> list[int]:
    max_train = float(y_train["time"].max())
    max_eval = float(y_eval["time"].max())
    min_eval = float(y_eval["time"].min())
    limit = min(max_train, max_eval)
    return [h for h in horizons if min_eval < h < limit]


def native_metrics(model, y_train, x_eval, y_eval, risk_sign: int = 1) -> dict:
    risk = risk_sign * model.predict(x_eval)
    c_index = concordance_index_censored(y_eval["event"], y_eval["time"], risk)[0]

    result: dict = {
        "rows": int(len(y_eval)),
        "events": int(y_eval["event"].sum()),
        "c_index": float(c_index),
        "max_followup_days": float(y_eval["time"].max()),
    }

    limit = min(float(y_train["time"].max()), float(y_eval["time"].max()))
    try:
        uno_c_index = concordance_index_ipcw(y_train, y_eval, risk, tau=limit * 0.99)[0]
        result["uno_c_index"] = float(uno_c_index)
    except ValueError:
        result["uno_c_index"] = None

    horizons = _usable_horizons(y_train, y_eval)
    result["horizons_evaluable_days"] = horizons
    if not horizons:
        result["integrated_brier_score"] = None
        result["brier_at_horizon"] = {}
        result["time_dependent_auc_at_horizon"] = {}
        return result

    times_grid, curve_values = survival_curve_arrays(model, x_eval)
    surv_at_horizons = step_eval_matrix(times_grid, curve_values, horizons)

    try:
        result["integrated_brier_score"] = float(
            integrated_brier_score(y_train, y_eval, surv_at_horizons, horizons)
        )
    except ValueError:
        result["integrated_brier_score"] = None

    try:
        _, brier_scores = brier_score(y_train, y_eval, surv_at_horizons, horizons)
        result["brier_at_horizon"] = {int(h): float(b) for h, b in zip(horizons, brier_scores)}
    except ValueError:
        result["brier_at_horizon"] = {}

    try:
        auc_scores, _ = cumulative_dynamic_auc(y_train, y_eval, risk, horizons)
        result["time_dependent_auc_at_horizon"] = {int(h): float(a) for h, a in zip(horizons, auc_scores)}
    except ValueError:
        result["time_dependent_auc_at_horizon"] = {}

    return result


def bootstrap_c_index(
    model, y_train, x_eval, y_eval, risk_sign: int = 1, n_boot: int = 200, seed: int = 42
) -> dict:
    risk = risk_sign * model.predict(x_eval)
    event = np.asarray(y_eval["event"])
    time = np.asarray(y_eval["time"])
    n = len(event)

    rng = np.random.default_rng(seed)
    scores = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        try:
            scores[i] = concordance_index_censored(event[idx], time[idx], risk[idx])[0]
        except ZeroDivisionError:
            scores[i] = np.nan

    valid = scores[~np.isnan(scores)]
    return {
        "point_estimate": float(concordance_index_censored(event, time, risk)[0]),
        "bootstrap_mean": float(np.mean(valid)),
        "ci_lower_2_5": float(np.percentile(valid, 2.5)),
        "ci_upper_97_5": float(np.percentile(valid, 97.5)),
        "std": float(np.std(valid)),
        "n_boot_valid": int(len(valid)),
    }


DEFAULT_RSF_PARAMS = dict(
    n_estimators=100,
    min_samples_split=40,
    min_samples_leaf=30,
    max_features="sqrt",
    n_jobs=-1,
    random_state=42,
)
DEFAULT_COX_PARAMS = dict(alpha=0.1, ties="efron")

MODEL_REGISTRY = {
    "random_survival_forest": {
        "cls": RandomSurvivalForest, "default_params": DEFAULT_RSF_PARAMS, "risk_sign": 1,
    },
    "cox_ph": {
        "cls": CoxPHSurvivalAnalysis, "default_params": DEFAULT_COX_PARAMS, "risk_sign": 1,
    },
}
DEFAULT_MODEL_NAMES = ["random_survival_forest", "cox_ph"]


def make_survival_target(dataset, mask):
    return Surv.from_arrays(
        event=dataset.loc[mask, "event_observed"].astype(bool).to_numpy(),
        time=dataset.loc[mask, "duration_days"].to_numpy(),
    )


def fit_models(
    x_train, y_train, model_names: list[str] | None = None, params: dict[str, dict] | None = None
) -> dict:
    names = model_names if model_names is not None else DEFAULT_MODEL_NAMES
    overrides = params or {}
    models: dict = {}
    for name in names:
        spec = MODEL_REGISTRY[name]
        model_params = overrides.get(name, spec["default_params"])
        models[name] = spec["cls"](**model_params).fit(x_train, y_train)
    return models


def evaluate_models(models: dict, y_train, x_val, y_val, x_test=None, y_test=None) -> dict:
    metrics: dict = {}
    for name, model in models.items():
        risk_sign = MODEL_REGISTRY.get(name, {}).get("risk_sign", 1)
        metrics[name] = {"validation": native_metrics(model, y_train, x_val, y_val, risk_sign=risk_sign)}
        if x_test is not None:
            metrics[name]["test"] = native_metrics(model, y_train, x_test, y_test, risk_sign=risk_sign)
    return metrics
