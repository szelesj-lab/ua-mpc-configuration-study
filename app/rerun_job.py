"""One re-run, executed in its own process by app/rerun.py (needs the summer GP models and a work copy).

    python -m app.rerun_job calibration <outdir>
    python -m app.rerun_job replay <stored run .json> <outdir>

The replay rebuilds the stored run from its own metadata (controller, overrides, plant, error scale, capacity,
re-planning interval, period) with the code of experiments/summer/engine.py, in this process, and writes
progress.jsonl (one line per MPC solve: its number and the SoC it starts from) while it runs and result.json at the end.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def _progress_hook(out: Path):
    """Wrap the MPC solver so that every solve reports its number and the SoC it starts from."""
    import MPC_costfunction
    orig = MPC_costfunction.optimize
    f = open(out / "progress.jsonl", "a", buffering=1)
    n = {"k": 0}

    def optimize(h, cfg, x_0=None, init=None):
        n["k"] += 1
        f.write(json.dumps({"n": n["k"], "soc": None if x_0 is None else float(x_0), "wall": time.time()}) + "\n")
        return orig(h, cfg, x_0=x_0, init=init)
    MPC_costfunction.optimize = optimize


def replay(stored: Path, out: Path):
    sys.path.insert(0, str(PKG / "experiments" / "summer"))
    import engine  # reads KAIROS_ROOT / KAIROS_SCEN, set by app/rerun.py
    from replay import build_data, load_config, named_periods, with_overrides
    meta = {k: v for k, v in json.loads(stored.read_text()).items() if k != "metrics"}
    period = meta.pop("period", "development"); meta.pop("start", None); meta.pop("end", None)
    (out / "steps").mkdir(parents=True, exist_ok=True)
    _progress_hook(out)
    # the same construction as engine.run(), for one spec and without a process pool
    base = with_overrides(load_config(), engine.SCEN)
    data = build_data(base)
    t0, t1 = named_periods(data, base)[period]
    k = (meta.get("error_scale", 1.0), meta.get("sigma_shape", ""))
    d = data if k[0] == 1.0 else engine.scale_errors(data, k[0])
    d = engine.reshape_sigma(d, k[1], t0, t1) if k[1] else d
    cfg = with_overrides(with_overrides(base, meta.get("overrides") or {}),
                         with_overrides({"replay": {"plant": meta.get("plant", engine.SCEN.get("replay", {}).get("plant", "physical"))}},
                                        meta.get("extra") or {}))
    tag = meta["tag"]
    engine._job((str(out), tag, meta["controller"], d, cfg, t0, t1, {**meta, "period": period, "start": t0.isoformat(), "end": t1.isoformat()}))
    (out / "result.json").write_text((out / "steps" / f"{tag}.json").read_text())


def calibration(out: Path):
    sys.path.insert(0, os.path.join(os.environ["KAIROS_ROOT"], "src"))
    import calibrate_uncertainty as cu
    from replay import build_data, load_config, named_periods
    cfg = load_config(); data = build_data(cfg); per = named_periods(data, cfg)
    res = cu.calibrate(data, cfg, *per["calibration"])
    ev = cu.evaluate(data, cfg, *per["development"], res["sigma_scale"], res["sigma_weather_coef"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({**res, **ev}, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.environ["KAIROS_ROOT"], "src"))
    if sys.argv[1] == "calibration":
        calibration(Path(sys.argv[2]))
    else:
        replay(Path(sys.argv[2]), Path(sys.argv[3]))
