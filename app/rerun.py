"""Level 2: re-run single experiments of the paper and compare them with the stored results.

Needs the trained summer GP models (models_summer.zip, attached to the GitHub release) unpacked to models_summer/
and the work copies of experiments/make_work.sh, which `prepare()` builds. Each re-run runs in its own process
(app/rerun_job.py) and writes into out/rerun/<id>/.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.pipeline import OUT, PKG, RESULTS

MODELS = Path(os.environ.get("SUMMER_MODELS", PKG / "models_summer"))
WORK_COPIES = Path(os.environ.get("WORK", PKG / "work"))
RERUN = OUT / "rerun"
ARCH = {"A": "renewable_buffer", "B": "self_consumption", "C": "setpoint"}

# a re-run reproduces when it lies within these tolerances of the stored result
TOL = {"net_cost_eur": ("rel", 0.001), "protection_h": ("abs", 0.5), "violation_pp_h": ("abs", 0.05),
       "extra_grid_eur": ("abs", 0.0005), "curtailed_pct": ("abs", 0.2)}
LABEL = {"net_cost_eur": "net cost (€)", "protection_h": "protection (h)", "violation_pp_h": "band violation (pp·h)",
         "extra_grid_eur": "extra grid cost (€)", "curtailed_pct": "curtailed (%)"}


def models_ok(quick: bool = False) -> tuple[bool, str]:
    sums = (PKG / "docs" / "models_summer.sha256").read_text().split("\n")
    missing, bad = [], []
    for line in filter(None, sums):
        h, name = line.split()
        p = MODELS / name
        if not p.exists():
            missing.append(name); continue
        if not quick and os.environ.get("SKIP_MODEL_CHECKSUM") != "1":
            d = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    d.update(chunk)
            if d.hexdigest() != h:
                bad.append(name)
    if missing:
        return False, f"missing in {MODELS}: {', '.join(missing)} (unpack models_summer.zip from the GitHub release there)"
    if bad:
        return False, f"checksum differs: {', '.join(bad)}"
    return True, f"summer models found in {MODELS}"


def prepare() -> tuple[bool, str]:
    ok, msg = models_ok()
    if not ok:
        return False, msg
    if not all((WORK_COPIES / X / "models" / "gpr_solar_model.joblib").exists() for X in "ABC"):
        p = subprocess.run(["bash", str(PKG / "experiments/make_work.sh")], env={**os.environ, "WORK": str(WORK_COPIES),
                           "SUMMER_MODELS": str(MODELS)}, capture_output=True, text=True)
        if p.returncode:
            return False, "make_work.sh failed: " + (p.stdout + p.stderr)[-800:]
    return True, "work copies ready in " + str(WORK_COPIES)


def est(mm: dict) -> int:
    """Rough duration in minutes: solves times the median solve time of the stored run, with a margin for slow solves."""
    return round(mm.get("solves", 0) * mm.get("solve_median_s", 0) * 2.5 / 60 + 1)


def experiments() -> list[dict]:
    """Re-runnable experiments: forecast calibration and every stored summer replay (final test, decision map)."""
    items = [{"id": "calibration", "group": "Forecast", "title": "Forecast calibration (Sections 4.2 and 5.1)",
              "kind": "calibration", "minutes": 3}]
    for X in "BCA":
        for f in sorted((RESULTS / "summer" / X / "final_test" / "steps").glob("*.json")):
            meta = json.loads(f.read_text()); mm = meta["metrics"]
            items.append({"id": f"final_{X}_{f.stem}", "group": f"Final test, {X}", "kind": "replay", "stored": str(f), "config": X,
                          "title": f"{meta['controller']} · {meta.get('plant', '')} plant" + (f" · {meta['tag']}" if meta["tag"].count("_") > 1 else ""),
                          "minutes": est(mm)})
        for f in sorted((RESULTS / "summer" / X / "decision_map_recovery" / "steps").glob("*.json")):
            meta = json.loads(f.read_text()); mm = meta["metrics"]
            items.append({"id": f"map_{X}_{f.stem}", "group": f"Decision map, {X}", "kind": "replay", "stored": str(f), "config": X,
                          "title": f"{meta['controller']} · {meta.get('cap')} Wh · error ×{meta.get('err')} · re-plan {int(meta.get('replan', 4)) // 4} h",
                          "minutes": est(mm)})
    return items


def _env(config: str = "B") -> dict:
    scen = {"limits": {"p_charge_max_w": 24.0}, "mpc": {"architecture": ARCH[config]},
            "replay": {"demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788, "plant": "bms"}}
    return {**os.environ, "KAIROS_ROOT": str(WORK_COPIES / config), "KAIROS_SCEN": json.dumps(scen),
            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONPATH": str(PKG)}


def start(exp_id: str) -> dict:
    e = next(x for x in experiments() if x["id"] == exp_id)
    out = RERUN / exp_id
    if out.exists():
        for f in out.rglob("*"):
            if f.is_file():
                f.unlink()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "app.rerun_job", "calibration", str(out)] if e["kind"] == "calibration" else \
          [sys.executable, "-m", "app.rerun_job", "replay", e["stored"], str(out)]
    log = open(out / "log.txt", "w")
    p = subprocess.Popen(cmd, cwd=PKG, env=_env(e.get("config", "B")), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (out / "state.json").write_text(json.dumps({"pid": p.pid, "started": time.time(), "id": exp_id}))
    return {"started": True, "id": exp_id}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a finished child that was not waited for is a zombie: treat as ended
        return Path(f"/proc/{pid}/status").read_text().find("State:\tZ") < 0
    except OSError:
        return True


def compare(stored: dict, new: dict) -> list[dict]:
    rows = []
    for k, (kind, tol) in TOL.items():
        if k not in stored or k not in new:
            continue
        a, b = float(stored[k]), float(new[k])
        dev = abs(b - a) / abs(a) if kind == "rel" and a else abs(b - a)
        rows.append({"kpi": LABEL[k], "stored": a, "rerun": b, "ok": dev <= tol,
                     "tolerance": f"{tol * 100:.1f} %" if kind == "rel" else f"± {tol}"})
    return rows


def status(exp_id: str) -> dict:
    out = RERUN / exp_id
    st = json.loads((out / "state.json").read_text()) if (out / "state.json").exists() else None
    if not st:
        return {"id": exp_id, "state": "not run"}
    e = next((x for x in experiments() if x["id"] == exp_id), {})
    prog = []
    if (out / "progress.jsonl").exists():
        prog = [json.loads(x) for x in (out / "progress.jsonl").read_text().splitlines() if x.strip()]
    expected = None
    if e.get("stored"):
        expected = json.loads(Path(e["stored"]).read_text())["metrics"].get("solves")
    res = {"id": exp_id, "title": e.get("title"), "group": e.get("group"), "started": st["started"],
           "elapsed_s": round(time.time() - st["started"]), "solves": len(prog), "expected_solves": expected,
           "soc": [[p["n"], p["soc"]] for p in prog[:: max(1, len(prog) // 400)]],
           "log": (out / "log.txt").read_text()[-1500:] if (out / "log.txt").exists() else ""}
    if (out / "result.json").exists():
        new = json.loads((out / "result.json").read_text())
        if e.get("kind") == "calibration":
            (OUT / "rerun").mkdir(parents=True, exist_ok=True)
            (OUT / "rerun" / "calibration.json").write_text(json.dumps(new, indent=1))
            res.update(state="done", comparison=[], note="compared with the paper on the Values tab (section 4.2 and 5.1)")
        else:
            res.update(state="done", comparison=compare(json.loads(Path(e["stored"]).read_text())["metrics"], new["metrics"]))
    elif _alive(st["pid"]):
        res["state"] = "running"
    else:
        res["state"] = "failed"
    return res


def all_status() -> list[dict]:
    return [status(p.name) for p in sorted(RERUN.glob("*")) if (p / "state.json").exists()] if RERUN.exists() else []
