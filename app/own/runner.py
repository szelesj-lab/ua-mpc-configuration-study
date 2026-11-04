"""Own-data simulations for the web interface: upload and check a load file, start a job, follow it.

Uploads live in out/own/uploads/<id>/, jobs in out/own/jobs/<id>/ (app/own/job.py runs each in its own process).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from app.own import bundles
from app.own.job import ARCH, has_det_bo
from app.own.profile import default_sizes, read, reference_ratios
from app.pipeline import OUT, PKG

OWN = OUT / "own"
UPLOADS, JOBS = OWN / "uploads", OWN / "jobs"
CONTROLLERS = ["det", "det_bo", "det_bo_sized", "ua"]
# rough minutes per controller and month on one core (about 720 solves of 0.5-5 s; machine-dependent)
MIN_PER_CHAIN_MONTH = 30


def upload(text: str, unit: str, tz: str, name: str = "") -> dict:
    series, rep = read(text, unit, tz)
    uid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    rep["name"] = name
    if series is None or rep["errors"]:
        return {"ok": False, "report": rep}
    d = UPLOADS / uid
    d.mkdir(parents=True, exist_ok=True)
    series.to_pickle(d / "load.pkl")
    (d / "upload.json").write_text(json.dumps(rep, indent=1, default=float))
    # a daily-mean sketch for the preview
    day = series.resample("1D").mean().dropna()
    week = series.groupby(series.index.tz_convert("Europe/Budapest").dayofweek * 96 +
                          series.index.tz_convert("Europe/Budapest").hour * 4 +
                          series.index.tz_convert("Europe/Budapest").minute // 15).mean()
    prev = {"daily": [[str(t.date()), round(float(v), 3)] for t, v in day.items()],
            "week": [round(float(v), 3) for v in week.reindex(range(672)).to_numpy()]}
    (d / "preview.json").write_text(json.dumps(prev))
    return {"ok": True, "id": uid, "report": rep, "preview": prev, "defaults": default_sizes(rep["mean_kw"]),
            "ratios": reference_ratios()}


def reference_upload() -> dict:
    """The load profile shipped with the package, as an upload, with the reference system's exact sizes."""
    from app.own import reference
    r = upload(reference.load_csv(), "kw", "UTC", "reference load profile (code/data/industrial_load_profile.csv)")
    if r["ok"]:
        f = UPLOADS / r["id"] / "upload.json"
        rep = json.loads(f.read_text())
        rep.update(reference=True, scale_mean_kw=reference.REF_MEAN_KW)
        f.write_text(json.dumps(rep, indent=1, default=float))
        r["report"] = rep
        r["defaults"] = dict(reference.REF_SIZES)
        r["reference"] = True
    return r


def options() -> dict:
    return {"months": bundles.months_available(), "bundles": bundles.status(),
            "configs": {X: {"architecture": ARCH[X], "det_bo": has_det_bo(X)} for X in "ABC"},
            "controllers": CONTROLLERS, "cpus": os.cpu_count() or 1, "min_per_chain_month": MIN_PER_CHAIN_MONTH}


def estimate(p: dict) -> dict:
    months = 1 if p["period"] == "final_test" else len(p["period"])
    new = [m for m in (["final_test"] if p["period"] == "final_test" else p["period"]) if not bundles.ready(m)]
    chains = sum(1 for X in p["configs"] for c in p["controllers"] if not (c.startswith("det_bo") and not has_det_bo(X)))
    w = max(1, int(p.get("workers") or os.cpu_count() or 1))
    sim = 0 if p["mode"] == "screen" else MIN_PER_CHAIN_MONTH * months * -(-chains // w)
    return {"training_min": 2 * len(new), "simulation_min": sim, "chains": chains, "workers": w}


def _check(p: dict):
    if p["mode"] not in ("quick", "screen"):
        raise ValueError("mode must be quick or screen")
    if p["period"] != "final_test":
        ok = set(bundles.months_available())
        if not p["period"] or any(m not in ok for m in p["period"]):
            raise ValueError(f"months must lie in {min(ok)} .. {max(ok)}")
        p["period"] = sorted(p["period"])
    if p.get("fill", "profile") not in ("profile", "data"):
        raise ValueError("fill must be profile or data")
    if not p["configs"] or any(X not in ARCH for X in p["configs"]):
        raise ValueError("choose at least one configuration of A, B, C")
    if any(c not in CONTROLLERS for c in p["controllers"]):
        raise ValueError("unknown controller")
    for k in ("battery_kwh", "power_kw", "pv_kwp", "wind_kw"):
        if float(p["sizes"].get(k, -1)) < 0:
            raise ValueError(f"{k} must be zero or positive")
    if float(p["sizes"]["battery_kwh"]) <= 0 or float(p["sizes"]["power_kw"]) <= 0:
        raise ValueError("battery capacity and power limit must be positive")


def start(upload_id: str, p: dict) -> dict:
    src = UPLOADS / upload_id
    if not (src / "load.pkl").exists():
        raise ValueError("unknown upload")
    p = {"mode": p.get("mode", "quick"), "period": p.get("period") or "final_test", "configs": p.get("configs") or ["B"],
         "controllers": p.get("controllers") or CONTROLLERS, "sizes": {k: float(v) for k, v in p["sizes"].items()},
         "initial_soc": p.get("initial_soc", 0.5), "fill": p.get("fill", "profile"), "workers": p.get("workers") or os.cpu_count() or 1,
         "upload_id": upload_id, "name": p.get("name", "")}
    _check(p)
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = JOBS / jid
    d.mkdir(parents=True)
    for f in ("load.pkl", "upload.json"):
        shutil.copy(src / f, d / f)
    p["estimate"] = estimate(p)
    (d / "params.json").write_text(json.dumps(p, indent=1))
    _launch(d)
    return {"id": jid, "estimate": p["estimate"]}


def _launch(d: Path):
    log = open(d / "log.txt", "a")
    env = {**os.environ, "PYTHONPATH": str(PKG), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    pr = subprocess.Popen([sys.executable, "-m", "app.own.job", str(d)], cwd=PKG, env=env, stdout=log,
                          stderr=subprocess.STDOUT, start_new_session=True)
    (d / "state.json").write_text(json.dumps({"pid": pr.pid, "started": time.time()}))


def resume(jid: str) -> dict:
    """Continue an interrupted job: finished months are kept, the rest is run again."""
    d = JOBS / jid
    if not (d / "params.json").exists():
        raise ValueError("unknown job")
    if status(jid)["state"] == "running":
        raise ValueError("the job is still running")
    for f in (d / "progress").glob("*.jsonl") if (d / "progress").exists() else []:
        f.unlink()
    _launch(d)
    return {"id": jid, "resumed": True}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/status").read_text().find("State:\tZ") < 0
    except OSError:
        return True


def status(jid: str) -> dict:
    d = JOBS / jid
    if not (d / "params.json").exists():
        return {"id": jid, "state": "unknown"}
    st = json.loads((d / "state.json").read_text())
    s = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {"stage": "starting"}
    p = json.loads((d / "params.json").read_text())
    exp = json.loads((d / "expected.json").read_text()) if (d / "expected.json").exists() else {}
    chains = []
    for k, n in exp.items():
        f = d / "progress" / f"{k}.jsonl"
        lines = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
        X, c = k.split("_", 1)
        done = len(list((d / "runs" / X).glob(f"{c}_*.json"))) if (d / "runs" / X).exists() else 0
        err = (d / "runs" / X / f"{c}.error.txt")
        chains.append({"key": k, "config": X, "controller": c, "solves": len(lines), "expected": n,
                       "months_done": done, "error": err.read_text()[-800:] if err.exists() else None,
                       "soc": [x["soc"] for x in lines[:: max(1, len(lines) // 300)]],
                       "rate": (len(lines) - 1) / (lines[-1]["wall"] - lines[0]["wall"]) if len(lines) > 5 and lines[-1]["wall"] > lines[0]["wall"] else None})
    # remaining time from the observed solve rate: chains run in parallel on the workers, the rest wait
    eta = None
    rates = [c["rate"] for c in chains if c["rate"]]
    if rates and s.get("stage") == "simulating":
        left = sum(max(c["expected"] - c["solves"], 0) for c in chains)
        eta = round(left / (sum(rates) / len(rates)) / max(1, min(int(p.get("workers") or 1), len(chains))) / 60)
    alive = _alive(st["pid"])
    state = "done" if s["stage"] == "done" else "failed" if s["stage"] == "failed" or not alive else "running"
    out = {"id": jid, "state": state, "stage": s, "params": p, "started": st["started"],
           "elapsed_s": round((s["time"] if state in ("done", "failed") and "time" in s else time.time()) - st["started"]), "chains": chains, "eta_min": eta,
           "log": (d / "log.txt").read_text()[-1500:] if (d / "log.txt").exists() else ""}
    if (d / "report.json").exists():
        out["report"] = json.loads((d / "report.json").read_text())
    return out


def jobs() -> list[dict]:
    if not JOBS.exists():
        return []
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        if (d / "params.json").exists():
            p = json.loads((d / "params.json").read_text())
            s = json.loads((d / "status.json").read_text())["stage"] if (d / "status.json").exists() else "starting"
            out.append({"id": d.name, "name": p.get("name"), "mode": p["mode"], "period": p["period"],
                        "configs": p["configs"], "stage": s, "report": (d / "report.json").exists()})
    return out


def file(jid: str, name: str) -> Path | None:
    if name not in ("report.md", "report.json", "monthly.csv"):
        return None
    f = JOBS / jid / name
    return f if f.exists() else None


SAMPLE_SPANS = {"week": ("2026-03-02", "2026-03-08"), "month": ("2026-03-01", "2026-03-31"),
                "year": ("2025-10-01", "2026-09-30")}


def sample_csv(span: str = "month", res: int = 60, unit: str = "kw", mean_kw: float = 250.0) -> tuple[bytes, str]:
    """A ready-to-upload example file in the accepted format, made from the load profile shipped with the package:
    timestamps in local time with their UTC offset (start of each interval), one load column, scaled to the given
    mean load. Returns (file content, file name)."""
    import pandas as pd
    span = span if span in SAMPLE_SPANS else "month"
    res = res if res in (15, 30, 60) else 60
    unit = unit if unit in ("kw", "kwh") else "kw"
    mean_kw = mean_kw if mean_kw > 0 else 250.0
    pr = pd.read_csv(PKG / "code" / "data" / "industrial_load_profile.csv", index_col=0)["load_pu"]
    pr.index = pd.to_datetime(pr.index, utc=True)
    s = pr.tz_convert("Europe/Budapest")
    a, b = SAMPLE_SPANS[span]
    s = s[(s.index >= pd.Timestamp(a, tz="Europe/Budapest")) & (s.index < pd.Timestamp(b, tz="Europe/Budapest") + pd.Timedelta(days=1))]
    s = s.resample(f"{res}min").mean().dropna()
    s = s * mean_kw / s.mean()
    col = "load_kw"
    if unit == "kwh":
        s, col = s * res / 60, "energy_kwh"
    head = ["# Example load file for the 'Your data' tab (load profile shipped with the package, scaled to "
            f"{mean_kw:g} kW mean).",
            "# Column 1: start of the interval, local time; the UTC offset (+01:00 / +02:00) is optional - without it,",
            "#   choose the time zone at upload. Column 2: " + ("average power in kW." if unit == "kw" else
                                                             "energy in the interval in kWh."),
            f"# Resolution {res} min; 15, 30 or 60 min are accepted. Lines starting with # are ignored."]
    body = "\n".join(f"{t.isoformat(sep=' ', timespec='minutes')},{v:.3f}" for t, v in s.items())
    name = f"sample_load_{span}_{res}min_{unit}.csv"
    return ("\n".join(head) + f"\ntimestamp,{col}\n" + body + "\n").encode(), name
