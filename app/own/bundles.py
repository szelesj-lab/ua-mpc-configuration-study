"""Forecast bundles: per simulated month, the GP trained on the weather before the month (walk-forward, as in the
winter test of the study) and the month's replay data; or the September 2026 test period with the summer models.

Bundles do not depend on the load or the sizes, so they are built once and cached in out/own/bundles/<month>/
(a copy of the controller code with its own models/ and results/). Training takes about 1-3 minutes per month.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

from app.pipeline import OUT, PKG, RESULTS

BUNDLES = Path(os.environ.get("OWN_BUNDLES", OUT / "own" / "bundles"))
TZ = "Europe/Budapest"
# months with weather, prices and forecasts: the first cutoff leaves four months of weather for training
FIRST, LAST = "2025-10", "2026-09"
FINAL = "final_test"


def months_available() -> list[str]:
    return [str(p) for p in pd.period_range(FIRST, LAST, freq="M")]


def month_bounds(m: str) -> tuple[str, str]:
    p = pd.Period(m, freq="M")
    return p.start_time.date().isoformat(), (p + 1).start_time.date().isoformat()


def root(m: str) -> Path:
    return BUNDLES / m


def ready(m: str) -> bool:
    r = root(m) / "results" / "rewind"
    return (r / "calibration.json").exists() and (r / "data.pkl").exists()


def trained(m: str) -> bool:
    return (root(m) / "models" / "gpr_wind_model.joblib").exists() and (root(m) / "models" / "gpr_solar_model.joblib").exists()


def _copy(r: Path):
    r.mkdir(parents=True, exist_ok=True)
    (r / "models").mkdir(exist_ok=True); (r / "results").mkdir(exist_ok=True)
    for d in ("src", "config"):
        if not (r / d).exists():
            shutil.copytree(PKG / "code" / d, r / d)
    if not (r / "data").exists():
        os.symlink(PKG / "code" / "data", r / "data")


def _env():
    return {**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}


def _run(cmd, cwd, log: Path):
    with open(log, "a") as f:
        f.write("$ " + " ".join(map(str, cmd)) + "\n"); f.flush()
        p = subprocess.run(list(map(str, cmd)), cwd=cwd, env=_env(), stdout=f, stderr=subprocess.STDOUT)
    if p.returncode:
        raise RuntimeError(f"{cmd[1]} failed (see {log}):\n" + log.read_text()[-1500:])


def final_test_stored() -> Path:
    return RESULTS / "summer" / "B" / "final_test" / "steps" / "bms_ua.json"


def build(m: str, log: Path, say=lambda s: None):
    """Train (if needed) and prepare the bundle of month m (or FINAL)."""
    if ready(m):
        return
    r = root(m)
    _copy(r)
    here = Path(__file__).resolve().parent
    if m == FINAL:
        from app.rerun import MODELS, models_ok
        ok, msg = models_ok(quick=True)
        if not ok:
            raise RuntimeError("the September 2026 test period needs the summer forecast models: " + msg)
        for f in MODELS.glob("*.joblib"):
            if not (r / "models" / f.name).exists():
                shutil.copy(f, r / "models" / f.name)
        say("preparing the final-test data (summer models)")
        _run([sys.executable, here / "prep_bundle.py", r, "final_test", final_test_stored()], r, log)
        return
    a, b = month_bounds(m)
    if not trained(m):
        say(f"training the forecast models for {m} (weather before {a})")
        _run([sys.executable, PKG / "experiments" / "winter" / "train_rewind.py", r, a], r, log)
    say(f"forecasts and calibration for {m}")
    _run([sys.executable, here / "prep_bundle.py", r, "month", a, b], r, log)


def load(m: str) -> tuple[pd.DataFrame, dict]:
    r = root(m) / "results" / "rewind"
    return pd.read_pickle(r / "data.pkl"), json.loads((r / "calibration.json").read_text())


def status() -> list[dict]:
    return [{"month": m, "trained": trained(m) or m == FINAL, "ready": ready(m)} for m in months_available() + [FINAL]]
