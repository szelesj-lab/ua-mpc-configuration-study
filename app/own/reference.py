"""Reference data set and reference check.

The package ships one load profile (code/data/industrial_load_profile.csv) with stored closed-loop results of the
same tool chain: the winter months October 2025 - March 2026 (configurations B and C) and the September 2026 test
period (A, B, C). When a run uses exactly that load with the reference system, the reference sizes, initial SoC 0.5
and only the days covered by the data, its results must equal the stored ones; the report then shows the comparison.
Any other input runs the same way, only without this comparison.
"""
from __future__ import annotations

import json

import pandas as pd

from app.own.profile import CELL, CELL_MEAN_W
from app.pipeline import PKG, RESULTS

REF_MEAN_KW = CELL_MEAN_W / 1000.0
REF_SIZES = {"battery_kwh": round(CELL["battery_wh"] / 1000, 12), "power_kw": round(CELL["power_w"] / 1000, 12),
             "pv_kwp": round(CELL["pv_w"] / 1000, 12), "wind_kw": round(CELL["wind_w"] / 1000, 12)}
WINTER_FIRST = "2025-10"
WINTER_MONTHS = 6
STORED = {"final_test": ("grid_only", "det", "det_bo", "ua"), "winter": ("grid_only", "det", "det_bo", "ua")}


def load_csv() -> str:
    """The reference load as an upload (kW of the reference system, UTC)."""
    pr = pd.read_csv(PKG / "code" / "data" / "industrial_load_profile.csv", index_col=0)["load_pu"]
    return "timestamp,load_kw\n" + "\n".join(f"{t},{v * REF_MEAN_KW!r}" for t, v in pr.items()) + "\n"


def _same_sizes(s: dict) -> bool:
    return all(abs(float(s[k]) - v) <= 1e-9 * max(v, 1e-12) for k, v in REF_SIZES.items())


def conditions(p: dict, up: dict) -> tuple[bool, str]:
    if not up.get("reference"):
        return False, ""
    why = []
    if not _same_sizes(p["sizes"]):
        why.append("the sizes differ from the reference system")
    if float(p.get("initial_soc", 0.5) if not isinstance(p.get("initial_soc"), dict) else -1) != 0.5:
        why.append("the initial SoC is not 0.5")
    if p.get("fill", "profile") != "data":
        why.append("days outside the data are filled from the profile")
    if p.get("mode") != "quick":
        why.append("screening runs no controllers")
    if p["period"] != "final_test" and p["period"][0] != WINTER_FIRST:
        why.append(f"the reference months start in {WINTER_FIRST} (the SoC carries over from it)")
    return (not why), "; ".join(why)


def stored(X: str, ctrl: str, period, k: int):
    """Stored metrics for configuration X, controller ctrl, month k of the run (or the September test period)."""
    if period == "final_test":
        f = RESULTS / "summer" / X / "final_test" / "steps" / f"bms_{ctrl}.json"
    elif k < WINTER_MONTHS:
        f = RESULTS / "winter" / X / f"{ctrl}_m{k + 1}.json"
    else:
        return None
    return json.loads(f.read_text())["metrics"] if f.exists() else None


def check(d) -> dict | None:
    from app.rerun import compare
    p = json.loads((d / "params.json").read_text()); up = json.loads((d / "upload.json").read_text())
    ok, why = conditions(p, up)
    if not up.get("reference"):
        return None
    if not ok:
        return {"applies": False, "reason": why, "rows": []}
    months = ["final_test"] if p["period"] == "final_test" else p["period"]
    rows = []
    for X in p["configs"]:
        for k, m in enumerate(months):
            for c in STORED["final_test" if p["period"] == "final_test" else "winter"]:
                run = d / "runs" / X / f"{c}_{m}.json"
                ref = stored(X, c, p["period"], k)
                if ref is None or not run.exists():
                    continue
                cmp = compare(ref, json.loads(run.read_text())["metrics"])
                rows.append({"config": X, "controller": c, "month": m, "ok": all(x["ok"] for x in cmp), "kpis": cmp})
    return {"applies": True, "rows": rows, "ok": bool(rows) and all(r["ok"] for r in rows),
            "note": "" if rows else "no stored result for these months or configurations (stored: winter 2025-10 .. 2026-03 "
                                    "for B and C, September 2026 for A, B and C)"}
