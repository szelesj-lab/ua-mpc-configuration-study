"""Shared paths and configuration loading.

Every script resolves files relative to the repository root, so they work
no matter which directory they are started from.
"""

import copy
import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "dataConfig.json"
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"
RESULTS_DIR = ROOT_DIR / "results"

# Optional: read secrets such as ENTSOE_API_KEY from a local .env file
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass


# Search ranges of the Bayesian tuning (tune_mpc.py): [low, high, "log" | "lin"]
_DET_PARAMS = {
    "weights.w_x": [1e-5, 1.0, "log"],
    "weights.w_ff": [1e-9, 1.0, "log"],
    "weights.w_delta_u": [1e-9, 0.1, "log"],
}

# Keys added in later versions. Missing ones are filled in when the config is read, so an older
# dataConfig.json keeps working and the new settings show up in the dashboard form.
CONFIG_DEFAULTS = {
    "live": {
        "deviation_abs_w": 2.0,
        "deviation_rel": 0.25,
        "deviation_soc": 0.05,
    },
    # older configs keep the original (absolute) uncertainty formulation
    "mpc": {
        "uncertainty_scaling": "absolute",
        "sigma_ref_w": 0.0,
        "sigma_scale": 1.0,
        "error_autocorr": 0.0,
        "sigma_model": "constant",
        "sigma_weather_coef": [0.0, 0.0, 0.0],
        "margin_window": "horizon",
        "margin_steps": 0,
        "soc_backoff": 0.0,
        "soc_recovery": True,
        "architecture": "renewable_buffer",
    },
    "replay": {
        "schedule_csv": "production_weekly.csv",
        "replan_steps": 4,
        "validation_days": 21,
        "test_days": 0,
        # four separate periods of the held-out data (all in days, local dates); calibration_days = 0
        # keeps the older two-period split (validation_days / test_days)
        "heldout_start": "",
        "calibration_days": 0,
        "tuning_days": 0,
        "development_days": 0,
        "workers": 0,
        "violation_penalty_eur_per_pp_h": 0.05,
        "solver_ftol": 1e-7,
        "plant": "physical",
        "demand_profile_csv": "",
        "demand_mean_w": 0.0,
    },
    "tuning": {
        "mode": "pareto",
        "objectives": ["net_cost_eur", "violation_pp_h", "curtailed_pct"],
        "trials": 24,
        "initial_points": 8,
        "episodes": 4,
        "episode_days": 3,
        "random_state": 42,
        "params_ua": {**_DET_PARAMS, "weights.lambda": [1.0, 1e5, "log"], "mpc.soc_chance_z": [0.0, 2.5, "lin"]},
        "params_det": dict(_DET_PARAMS),
        "params_det_bo": {**_DET_PARAMS, "mpc.soc_backoff": [0.0, 0.3, "lin"]},
        "params_det_base": dict(_DET_PARAMS),
        "params_oracle": dict(_DET_PARAMS),
    },
}


def with_defaults(cfg: dict, defaults: dict = CONFIG_DEFAULTS) -> dict:
    for key, val in defaults.items():
        if key not in cfg:
            cfg[key] = copy.deepcopy(val)
        elif isinstance(val, dict) and isinstance(cfg[key], dict) and not key.startswith("params_"):
            with_defaults(cfg[key], val)
    return cfg


def load_config(config_path=CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return with_defaults(json.load(f))


def get_entsoe_api_key() -> str:
    key = os.environ.get("ENTSOE_API_KEY")
    if not key:
        raise RuntimeError(
            "ENTSOE_API_KEY is not set. Copy .env.example to .env and add your "
            "ENTSO-E Transparency Platform API key, or set it as an environment variable."
        )
    return key
