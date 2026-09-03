from __future__ import annotations

import json
import sys

import joblib
import pandas as pd
from catboost import CatBoostClassifier

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder


def risk_level(probability: float, cutoffs: dict[str, float]) -> str:
    if probability >= cutoffs["high"]:
        return "HIGH"
    if probability >= cutoffs["medium"]:
        return "MEDIUM"
    return "LOW"


_LOADED_FAILURE: tuple[CatBoostClassifier, object, dict] | None = None
_FLEET: object = None
_ITEM_TYPE_DENSITY: object = None


def _item_type_density_snapshot(data_end):
    global _ITEM_TYPE_DENSITY
    if _ITEM_TYPE_DENSITY is not None:
        return _ITEM_TYPE_DENSITY

    events = data_reader.get_events()
    cycles = data_reader.get_cycles()
    episodes = data_reader.get_failure_episodes()
    _ITEM_TYPE_DENSITY = feature_builder.item_type_density_snapshot(cycles, events, episodes, data_end)
    return _ITEM_TYPE_DENSITY


def _fleet_snapshot(data_end):
    global _FLEET
    if _FLEET is not None:
        return _FLEET

    _, _, metadata = _load_failure_model()
    directory = config.FAILURE_MODEL_DIR / metadata["model_version"]
    stored = directory / "fleet_snapshot.csv"
    if stored.exists() and metadata.get("fleet_snapshot_at") == str(data_end):

        snapshot = pd.read_csv(stored, dtype={"item_model_code_clean": str})
        if _covers_known_models(snapshot, metadata):
            _FLEET = snapshot
            return _FLEET

    cycles = data_reader.get_cycles()
    episodes = data_reader.get_failure_episodes()
    _FLEET = feature_builder.fleet_snapshot(cycles, episodes, data_end)
    return _FLEET


def _covers_known_models(snapshot, metadata: dict) -> bool:

    known = set(metadata.get("part_model_support", {}))
    if not known:
        return True
    overlap = len(known & set(snapshot["item_model_code_clean"].astype(str)))
    return overlap >= 0.8 * len(known)


class FailureNotScorable(LookupError):
    pass


def _load_failure_model() -> tuple[CatBoostClassifier, object, dict]:
    global _LOADED_FAILURE
    if _LOADED_FAILURE is not None:
        return _LOADED_FAILURE

    pointer = config.FAILURE_MODEL_DIR / "CURRENT"
    if not pointer.exists():
        raise FileNotFoundError(
            f"Belum ada model kerusakan di {config.FAILURE_MODEL_DIR}. "
            "Jalankan dulu: python train.py"
        )
    directory = config.FAILURE_MODEL_DIR / pointer.read_text(encoding="utf-8").strip()

    model = CatBoostClassifier()
    model.load_model(str(directory / "model.cbm"))
    calibrator = joblib.load(directory / "calibrator.joblib")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    _LOADED_FAILURE = (model, calibrator, metadata)
    return _LOADED_FAILURE


def predict(item_id: str) -> dict:
    model, calibrator, metadata = _load_failure_model()

    data_end = data_reader.get_dataset_max_event_on()
    cycles = data_reader.get_cycles(item_id, data_end)
    if cycles.empty:
        raise FailureNotScorable(f"PART '{item_id}' tidak ditemukan di database.")

    events = data_reader.get_events(item_id)
    snapshot = feature_builder.current_observations(cycles, events)
    if snapshot.empty:
        raise FailureNotScorable(
            f"PART '{item_id}' sedang tidak terpasang (sudah rusak atau sudah "
            "dipasang ulang), jadi tidak ada risiko yang perlu diperkirakan."
        )

    snapshot = feature_builder.attach_history(snapshot, events)
    snapshot = feature_builder.attach_degradation_history(snapshot, cycles, events)
    snapshot = feature_builder.attach_fleet_snapshot(snapshot, _fleet_snapshot(data_end))
    snapshot = feature_builder.attach_item_type_density_snapshot(
        snapshot, events, _item_type_density_snapshot(data_end)
    )
    support = feature_builder.part_model_support(
        snapshot, metadata["part_model_support"]
    )

    steps = max(config.PREDICTION_HORIZON_DAYS) // config.OBSERVATION_STEP_DAYS
    survival = 1.0
    cumulative_risk: dict[int, float] = {}
    for step in range(steps):
        features = feature_builder.project_features(snapshot, support, step)

        features = features[metadata["features"]]
        raw = float(model.predict_proba(features)[:, 1][0])
        hazard = float(calibrator.predict([raw])[0])
        survival *= 1.0 - hazard
        cumulative_risk[(step + 1) * config.OBSERVATION_STEP_DAYS] = 1.0 - survival

    probabilities = {
        f"failure_probability_{days}d": round(cumulative_risk[days], 4)
        for days in config.PREDICTION_HORIZON_DAYS
    }

    return {
        "item_id": snapshot["item_identifier_clean"].iloc[0],
        **probabilities,
        "risk_level": risk_level(
            probabilities["failure_probability_30d"], metadata["risk_cutoffs"]
        ),
        "model_version": metadata["model_version"],
        "as_of": str(snapshot["observation_on"].iloc[0]),
        "installed_on": str(snapshot["installed_on"].iloc[0]),
    }


def clear_fleet_cache() -> None:
    global _FLEET, _ITEM_TYPE_DENSITY
    _FLEET = None
    _ITEM_TYPE_DENSITY = None


load_failure_model = _load_failure_model
fleet_snapshot = _fleet_snapshot
item_type_density_snapshot = _item_type_density_snapshot


def main() -> int:
    if len(sys.argv) < 2:
        print("Pemakaian: python -m partrisk.engines.predict <item_id>")
        return 1
    item_id = sys.argv[1]
    try:
        result = predict(item_id)
    except FailureNotScorable as error:
        print(f"[TIDAK BISA DISKOR] {error}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
