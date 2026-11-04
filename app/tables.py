"""The paper's tables, recomputed cell by cell from the result files.

Each function returns the rows of one table in the paper's order, as lists of numbers in the order of the
numeric cells printed in the paper (app/paper_reference.json). `check_tables()` compares them cell by cell,
rounded to the decimals printed in the paper.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from app.pipeline import RESULTS

EXT = RESULTS / "extended"
SUM = RESULTS / "summer"
WIN = RESULTS / "winter"
C2 = RESULTS / "production_schedule"
REF = json.loads((Path(__file__).with_name("paper_reference.json")).read_text(encoding="utf-8"))
H = 0.25  # hours per step


def j(p):
    return json.load(open(p))


def met(p):
    return j(p)["metrics"]


# ---------------------------------------------------------------- Table 2: periods
def t_periods():
    e = pd.read_csv(RESULTS / "analysis/energy.csv").set_index("period")
    names = ["calibration", "tuning", "development", "final_test"] + [f"winter {m}" for m in ("2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03")]
    rows = []
    for n in names:
        r = e.loc[n]
        rows.append([r.days, r.load_wh / 1000, (r.pv_wh + r.wind_wh) / 1000, r.ren_share_pct, r.surplus_hours_pct, r.price_mean])
    return rows


# ---------------------------------------------------------------- Table 4: selected points
def t_weights():
    rows = []
    for X, ctrls in (("A", ("det", "det_bo", "ua")), ("B", ("det", "det_bo", "ua")), ("C", ("det", "ua"))):
        sel = j(SUM / X / "final_test/preregistration.json")["selected"]["primary_threshold_pp_h"]
        for c in ctrls:
            s = sel[c]
            pts = j(SUM / X / f"tuning/{c}_pareto.json")["points"]
            o = next(p["overrides"] for p in pts if p["trial"] == s["trial"])
            w = o["weights"]; r = [w["w_x"], w["w_ff"], w["w_delta_u"]]
            if c == "det_bo":
                r.append(o["mpc"]["soc_backoff"] if "soc_backoff" in o.get("mpc", {}) else o["mpc"].get("soc_margin", np.nan))
            if c == "ua":
                r += [o["mpc"]["soc_chance_z"], w["lambda"]]
            rows.append(r + [s["dev_cost"], s["dev_prot_h"]])
    return rows


# ---------------------------------------------------------------- Table 5: calibration per channel
def t_calib():
    d = pd.read_csv(RESULTS / "analysis/extended/calib_channels.csv")
    rows = []
    for per in ("development", "final_test"):
        for ch in ("solar", "wind", "total"):
            r = d[(d.period == per) & (d.channel == ch)].iloc[0]
            rows.append([r.hours, r.rmse_w, r.cov1, r.cov2, r.cov2_largest_quarter])
    return rows


# ---------------------------------------------------------------- Table 6: final test
@cache
def split():
    return pd.read_csv(RESULTS / "analysis/protection_split.csv")


def prot(study, X, v):
    s = split(); r = s[(s.study == study) & (s.config == X) & (s.variant == v)].iloc[0]
    return r.prot_lo_h, r.prot_up_h


def t_final():
    rows = []
    order = {"A": ["grid_only", "det", "det_bo", "ua", "ua_rho_only", "ua_margin_only", "oracle"],
             "B": ["grid_only", "det", "det_bo", "ua", "ua_rho_only", "ua_margin_only", "oracle"],
             "C": ["grid_only", "det", "ua", "ua_rho_only", "ua_margin_only", "oracle"]}
    for X, vs in order.items():
        for v in vs:
            m = met(SUM / X / f"final_test/steps/bms_{v}.json"); lo, up = prot("final", X, v)
            r = [m["net_cost_eur"], lo, up, 1000 * m["extra_grid_eur"], m["curtailed_pct"], m["cycles_per_day"]]
            if v != "grid_only":
                r.append(m["solver_success_pct"])
            rows.append(r)
    return rows


# ---------------------------------------------------------------- Table 7: bootstrap, final test
def boot_row(b, ref, plant="bms"):
    g = b[(b.plant == plant) & (b.a == "ua") & (b.b == ref)].set_index("kpi")
    c, p, e = g.loc["cost"], g.loc["prot"], g.loc["extra"]
    return [c.diff_pct, 1000 * c.lo_total, 1000 * c.hi_total, H * p.lo_total, H * p.hi_total, 1000 * e.lo_total, 1000 * e.hi_total]


def t_boot():
    rows = []
    for X, ref in (("A", "det"), ("A", "det_bo"), ("B", "det"), ("B", "det_bo"), ("C", "det")):
        rows.append(boot_row(pd.read_csv(SUM / X / "final_test/paired_bootstrap.csv"), ref))
    return rows


# ---------------------------------------------------------------- Table 8: indicators
def t_litind():
    d = pd.read_csv(EXT / "indicators/indicator_benchmark.csv").set_index("indicator")
    return [[d.loc[i, "auc_30"], d.loc[i, "loo"], 30, d.loc[i, "auc_B_second"]] for i in ("r_E", "r_E_day", "S24", "Rday", "sigma_mean")]


# ---------------------------------------------------------------- Table 9: out of sample
def t_oos():
    eg = pd.read_csv(EXT / "ratio/effective_gamma.csv").set_index("period")
    oos = pd.read_csv(EXT / "ratio/rE_outofsample.csv")
    wd = pd.read_csv(EXT / "ratio/winter_B_diff.csv").set_index("m")
    rows = [[eg.loc["final_test", "r_E"], *prot_pair("A", "det_bo")],
            [eg.loc["final_test", "r_E"], *prot_pair("B", "det_bo")],
            [0.0, *prot_pair("C", "det")]]
    b = oos[oos.config == "B"].set_index("period")
    for m in range(1, 7):
        r = b.loc[f"winter m{m}"]; w = wd.loc[m]
        rows.append([r.r_E, r.ref_prot_h, r.ua_prot_h, -w.hi, -w.lo])  # winter_B_diff: back-off minus UA-MPC
    c = oos[oos.config == "C"]
    rows.append([0.0, c.ref_prot_h.min(), c.ref_prot_h.max(), c.ua_prot_h.min(), c.ua_prot_h.max()])
    return rows


def prot_pair(X, ref):
    a = met(SUM / X / f"final_test/steps/bms_{ref}.json")["protection_h"]; u = met(SUM / X / "final_test/steps/bms_ua.json")["protection_h"]
    return [a, u]


# ---------------------------------------------------------------- Table 10: pre-registered tests
def t_prereg():
    d = pd.read_csv(EXT / "preregistered/figs/fig16_data.csv")
    d = d[d.set != "Decision map (development)"]
    rows, tot = [], np.zeros(5)
    for name in ("New cells", "Error correlation", "Back-off sweep"):
        g = d[d.set.str.startswith(name)]
        sweep = name == "Back-off sweep"
        pred = ["tie" if (x <= 1 + 1e-9 if sweep else x < 1) else ("difference" if (x >= 1.5 - 1e-9 if sweep else True) else None) for x in g.Gamma]
        pred = pd.Series(pred, index=g.index)
        firm = pred.notna(); ties = firm & (pred == "tie"); diffs = firm & (pred == "difference")
        r = np.array([firm.sum(), (ties & (g.label == "tie")).sum(), ties.sum(),
                      (diffs & (g.label == "difference")).sum(), diffs.sum()], dtype=float)
        rows.append(list(r)); tot += r
    rows.append(list(tot))
    return rows


# ---------------------------------------------------------------- Table 11: T1
def winter_run(ctrl, m, base=WIN / "B"):
    return met(base / f"{ctrl}_m{m}.json")


def t_t1():
    oos = pd.read_csv(EXT / "ratio/rE_outofsample.csv"); b = oos[oos.config == "B"].set_index("period")
    rows, tot = [], np.zeros(6)
    for m in range(2, 7):
        t = j(EXT / f"preregistered/t1/det_bo_sized_m{m}.json"); rs = t["metrics"]
        r_past = b.loc[f"winter m{m - 1}", "r_E"]
        bo, ua = winter_run("det_bo", m), winter_run("ua", m)
        vals = [rs["net_cost_eur"], rs["protection_h"], bo["net_cost_eur"], bo["protection_h"], ua["net_cost_eur"], ua["protection_h"]]
        rows.append([r_past, t["b"], b.loc[f"winter m{m}", "r_E"] * 0.8 / t["b"], *vals]); tot += vals
    rows.append(list(tot))
    f = j(EXT / "preregistered/t1/steps/det_bo_sized_final.json")
    eg = pd.read_csv(EXT / "ratio/effective_gamma.csv").set_index("period")
    bo, ua = met(SUM / "B/final_test/steps/bms_det_bo.json"), met(SUM / "B/final_test/steps/bms_ua.json")
    rows.append([eg.loc["tuning", "r_E"], f["b"], f["metrics"]["net_cost_eur"], f["metrics"]["protection_h"],
                 bo["net_cost_eur"], bo["protection_h"], ua["net_cost_eur"], ua["protection_h"]])
    return rows


# ---------------------------------------------------------------- Table 12: winter
def t_winter():
    rows = []
    for X, ctrls in (("B", ("grid_only", "det", "det_bo", "ua")), ("C", ("grid_only", "det", "ua"))):
        for c in ctrls:
            ms = [winter_run(c, m, WIN / X) for m in range(1, 7)]
            lo, up = prot("winter", X, c)
            r = [sum(x["net_cost_eur"] for x in ms), lo, up, 1000 * sum(x["extra_grid_eur"] for x in ms), sum(x["curtailed_kwh"] for x in ms)]
            if c != "grid_only":
                r.append(100 * sum(x["solver_success_pct"] * x["solves"] for x in ms) / 100 / sum(x["solves"] for x in ms))
            rows.append(r)
    return rows


# ---------------------------------------------------------------- Supplementary: T2 and the second load case
def t_t2():
    rows = []
    mp = pd.read_csv(C2 / "decision_map_recovery/map.csv")
    for cap, err, rp in ((36, 1.0, 4), (72, 2.0, 4), (36, 2.0, 4), (72, 1.0, 16)):
        r = mp[(mp.cap_wh == cap) & (mp.error_scale == err) & (mp.replan_steps == rp)].iloc[0]
        v = [met(EXT / f"preregistered/t2/steps/bsw2_det_bo_cap{cap}_err{err}_rp{rp}_G{g}.json")["violation_pp_h"] for g in (0.5, 0.75, 1.0, 1.5, 2.0)]
        rows.append([r.r_E, *v, r.ua_viol])
    return rows


def t_c2final():
    s = pd.read_csv(C2 / "final_test/summary.csv")
    def g(plant, v):
        return s[(s.plant == plant) & (s.variant == v)].iloc[0]
    rows = []
    for v in ("grid_only", "rule", "det", "det_bo", "ua", "ua_rho_only", "ua_margin_only", "ua_secondary", "oracle"):
        p, b = g("physical", v), g("bms", v)
        r = [p.net_cost_eur, p.violation_pp_h, p.max_violation_pp, b.net_cost_eur, b.protection_steps, 1000 * b.extra_grid_eur]
        if v not in ("grid_only", "rule"):
            r.append(p.solver_success_pct)
        rows.append(r)
    return rows


def t_c2boot():
    b = pd.read_csv(C2 / "final_test/paired_bootstrap.csv"); rows = []
    for ref in ("det", "det_bo", "ua_rho_only", "ua_margin_only", "oracle"):
        def k(plant, kpi):
            return b[(b.plant == plant) & (b.a == "ua") & (b.b == ref) & (b.kpi == kpi)].iloc[0]
        c, v, p, e = k("physical", "cost"), k("physical", "viol"), k("bms", "prot"), k("bms", "extra")
        rows.append([c.diff_pct, 1000 * c.lo_total, 1000 * c.hi_total, v.diff_pct, v.lo_total, v.hi_total,
                     p.diff_pct, p.lo_total, p.hi_total, e.diff_pct, 1000 * e.lo_total, 1000 * e.hi_total])
    return rows


def t_c2map():
    mp = pd.read_csv(C2 / "decision_map_recovery/map.csv")
    keys = [(144, .5, 4), (72, .5, 4), (144, 1, 4), (36, .5, 4), (72, 1, 4), (144, 2, 4), (36, 1, 4), (72, 2, 4), (72, 1, 16), (36, 2, 4)]
    rows = []
    for cap, err, rp in keys:
        r = mp[(mp.cap_wh == cap) & (mp.error_scale == err) & (mp.replan_steps == rp)].iloc[0]
        rows.append([r.r_E, r.r_P, r.bo_viol, r.ua_viol, r.cost_diff_pct])
    return rows


def t_c2var():
    v = pd.read_csv(C2 / "variability/variability.csv")
    return [[r.r_E, r.bo_viol, r.constant_viol, r.ua_viol, r.amplified_viol, r.ua_cost] for r in v.itertuples()]


# ---------------------------------------------------------------- Supplementary: sensitivity and tariffs
def t_sens():
    d = pd.read_csv(EXT / "sensitivity/sens.csv")
    rows = []
    for X, ctrls in (("A", ("det", "det_bo", "ua")), ("B", ("det", "det_bo", "ua")), ("C", ("det", "ua"))):
        for c in ctrls:
            r = []
            for sc in ("base", "solver", "load10", "renm10", "renp10") + (("chan",) if c == "ua" and X != "C" else ()):
                g = d[(d.config == X) & (d.ctrl == c) & (d.scenario == sc)].iloc[0]; r += [g.cost, g.prot]
            rows.append(r)
    return rows


def t_tariff():
    e = pd.read_csv(EXT / "tariffs/expost.csv"); e = e[(e.test == "summer") & (e.ctrl == "ua")].set_index("config")
    rows = [[e.loc["A", "base"], e.loc["A", "export"], e.loc["A", "demand"]]]
    for X in ("B", "C"):
        st = SUM / X / "final_test/steps"
        g = pd.read_csv(st / "bms_grid_only.csv"); g.index = pd.to_datetime(g.t, utc=True)
        month = g.index.tz_convert("Europe/Budapest").strftime("%Y-%m")
        rate = 0.2 * met(st / "bms_grid_only.json")["net_cost_eur"] / g.grid_w.groupby(month).max().sum()
        q = pd.read_csv(EXT / f"tariffs/glim_{X}/steps/glim_ua.csv"); q.index = pd.to_datetime(q.t, utc=True)
        qm = q.index.tz_convert("Europe/Budapest").strftime("%Y-%m")
        lim = met(EXT / f"tariffs/glim_{X}/steps/glim_ua.json")["net_cost_eur"]
        rows.append([e.loc[X, "base"], e.loc[X, "export"], e.loc[X, "demand"], lim, lim + rate * q.grid_w.groupby(qm).max().sum()])
    return rows


TABLES = {  # label: (paper number, title, function)
    "tab:periods": ("Table 2", "Periods", t_periods),
    "tab:weights": ("Table 4", "Selected operating points", t_weights),
    "tab:calib": ("Table 5", "Calibration per channel", t_calib),
    "tab:final": ("Table 6", "Final test, BMS plant", t_final),
    "tab:boot": ("Table 7", "Paired bootstrap, final test", t_boot),
    "tab:litind": ("Table 8", "r_E against sizing indicators", t_litind),
    "tab:oos": ("Table 9", "Bounds out of sample", t_oos),
    "tab:prereg": ("Table 10", "Pre-registered predictions", t_prereg),
    "tab:t1": ("Table 11", "Prospective sizing (T1)", t_t1),
    "tab:winter": ("Table 12", "Walk-forward winter test", t_winter),
    "tab:t2": ("Table S1", "Back-off sweep, second load case (T2)", t_t2),
    "tab:c2final": ("Table S2", "Second load case, final test", t_c2final),
    "tab:c2boot": ("Table S3", "Second load case, bootstrap", t_c2boot),
    "tab:c2map": ("Table S4", "Second load case, decision map", t_c2map),
    "tab:c2var": ("Table S5", "Second load case, variability", t_c2var),
    "tab:sens": ("Table S6", "Sensitivity runs", t_sens),
    "tab:tariff": ("Table S7", "Tariff variants", t_tariff),
}


# ---------------------------------------------------------------- comparison
def fmt_like(ref: str, x: float) -> str:
    if "e" in ref:
        mant, ex = ref.split("e"); d = len(mant.split(".")[1]) if "." in mant else 0
        return f"{x:.{d}e}".replace("e-0", "e-").replace("e+0", "e")
    d = len(ref.split(".")[1]) if "." in ref else 0
    s = f"{x:.{d}f}"
    return "0" if float(s) == 0 and d == 0 else s


def same(ref: str, x: float) -> bool:
    if "e" in ref:
        return fmt_like(ref, x) == ref.lstrip("+")
    return float(fmt_like(ref, x)) == float(ref)


def check_tables() -> list[dict]:
    out = []
    for label, (no, title, fn) in TABLES.items():
        ref = REF.get(label)
        if not ref:
            continue
        try:
            got = fn(); err = ""
        except Exception as e:  # reported, not hidden
            got, err = [], f"{type(e).__name__}: {e}"
        rows, n_ok, n = [], 0, 0
        for i, rr in enumerate(ref["rows"]):
            g = got[i] if i < len(got) else []
            cells = []
            for k, rv in enumerate(rr["values"]):
                if k < len(g) and g[k] is not None and not (isinstance(g[k], float) and np.isnan(g[k])):
                    ok = same(rv, float(g[k])); s = fmt_like(rv, float(g[k]))
                else:
                    ok, s = False, "–"
                cells.append({"paper": rv, "reproduced": s, "ok": ok}); n += 1; n_ok += ok
            rows.append({"label": rr["label"], "cells": cells, "shape_ok": len(g) == len(rr["values"])})
        out.append({"label": label, "no": no, "title": title, "caption": ref["caption"], "cells": n, "cells_ok": n_ok,
                    "rows": rows, "error": err})
    return out


if __name__ == "__main__":
    for t in check_tables():
        print(f"{t['no']:<9} {t['title']:<40} {t['cells_ok']:>3}/{t['cells']:<3} {t['error']}")
        for r in t["rows"]:
            bad = [f"{c['paper']}≠{c['reproduced']}" for c in r["cells"] if not c["ok"]]
            if bad or not r["shape_ok"]:
                print("     ", r["label"][:30], bad[:8], "" if r["shape_ok"] else "(shape)")
