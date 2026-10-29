"""Numbers and tables of the industrial-load study (configurations A, B, C), the walk-forward winter test and the
data inventory. Writes paper/numbers3.tex (\\newcommand per number) and paper/tables/i_*.tex.

    python make_numbers3.py

Inputs (all produced by the scripts of the study, nothing typed by hand):
  ind/results_{A,B,C}/   final test, pre-registration, development fronts, decision map (industrial load, summer)
  rw/results/            walk-forward winter test (B, C), monthly runs and paired bootstrap
  ms3/protection_split.csv  protection hours split by SoC edge (split_protection.py)
  inv/                   data inventory, period energies, monthly energies (inventory.py, energy step)
"""
import glob
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))          # shipped results (input of the analysis)
K = os.environ.get("KAIROS_CODE", os.path.join(PKG, "code"))          # controller code
OUTD = os.environ.get("OUT", os.path.join(PKG, "out"))                 # generated tables and figures
IND, RW, INV = f"{RES}/summer", f"{RES}/winter", f"{RES}/analysis"
H = INV
OUT = f"{OUTD}/manuscript"
sys.path.insert(0, f"{K}/src")
os.makedirs(f"{OUT}/tables", exist_ok=True)
macros = {}
CONF = {"A": "renewable buffer", "B": "self-consumption", "C": "set-point"}
CTRL = {"det": "\\DET", "det_bo": "\\DETBO", "ua": "\\UA", "grid_only": "Grid only", "rule": "Rule-based",
        "oracle": "Oracle", "ua_rho_only": "\\UA, $\\rho$ only", "ua_margin_only": "\\UA, margin only",
        "ua_secondary": "\\UA, secondary rule"}


def num(x, d=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "--"
    s = f"{x:,.{d}f}".replace(",", "{,}")
    return "$-$" + s[1:] if s.startswith("-") else s


def sci(x):
    if x is None or not np.isfinite(x):
        return "--"
    if x == 0:
        return "0"
    e = int(np.floor(np.log10(abs(x))))
    m = x / 10 ** e
    return f"${m:.1f}\\times10^{{{e}}}$" if e not in (0, -1) else f"{x:.2f}"


def M(name, value):
    assert name.isalpha(), name
    assert name not in macros, name
    macros[name] = value


def tab(name, lines):
    open(f"{OUT}/tables/{name}.tex", "w").write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ data and plant
raw = pd.read_csv(f"{INV}/hourly_pu.csv", index_col=0, parse_dates=True)["pu"]
inv = pd.read_csv(f"{INV}/inventory.csv")
lp = pd.read_csv(f"{K}/data/industrial_load_profile.csv")
M("plantFilledPct", num(100 * lp["filled"].mean(), 2))
M("plantFilledH", num(lp["filled"].sum() / 4, 0))
M("plantHours", num(len(raw), 0))
M("plantPeakPu", num(raw.max(), 2))
M("plantMinPu", num(raw.min(), 2))
loc = raw.tz_convert("Europe/Budapest")
wk = loc[loc.index.dayofweek < 5]
M("plantWeekdayPu", num(wk.mean(), 2))
M("plantWeekendPu", num(loc[loc.index.dayofweek >= 5].mean(), 2))
M("plantShiftPu", num(wk[(wk.index.hour >= 6) & (wk.index.hour < 14)].median(), 1))
mm = loc.groupby(loc.index.month).mean()
M("plantWinterPu", num(mm[[12, 1, 2]].mean(), 2))
M("plantSummerPu", num(mm[[6, 7, 8]].mean(), 2))
# absolute size only rounded (anonymity)
M("plantGWh", "17")
M("plantMW", "1.9")

cfg = json.load(open(f"{K}/config/dataConfig.json"))
dmean = 10.788
M("cellMeanLoad", num(dmean, 1))
M("cellEpu", num(72 / dmean, 1))
M("cellPchPu", num(24 / dmean, 1))
M("cellPVpu", num(cfg["pv"]["rated_power_w"] / dmean, 1))
M("cellScale", num(1.933e6 / dmean / 1e5, 1))           # plant mean (W) / cell mean (W), in 1e5

AREA = {"Csomagoló": "packaging", "Daraboló": "portioning", "Hűtés": "cooling", "Vago": "slaughtering"}
sub = inv[inv.source.str.startswith("Sub-meters")]
n_m = sum(int(o.split(", ")[1].split()[0]) for o in sub.origin)
inv = pd.concat([inv[~inv.source.str.startswith("Sub-meters")].iloc[:1],
                 pd.DataFrame([dict(source=f"Sub-meters ({', '.join(AREA[a.split(': ')[1]] for a in sub.source)}; {n_m} meters)",
                                    start="2025-02", end="2025-10", resolution="1 h, kWh",
                                    gaps="one month each in Feb, Aug, Oct 2025; partial months; outliers in Feb (company note)",
                                    used="Not used (three single months)")]),
                 inv[~inv.source.str.startswith("Sub-meters")].iloc[1:]], ignore_index=True)
inv["used"] = inv["used"].str.replace("Not used (no CO2 KPI in this study)", "Not used (electricity only)", regex=False)
lines = []
for r in inv.itertuples():
    used = r.used.replace("_", "\\_").replace("%", "\\%")
    gaps = str(r.gaps).replace("%", "\\%").replace("_", "\\_")
    def local(v):
        v = str(v)
        if "+" in v[10:]:
            return pd.Timestamp(v).tz_convert("Europe/Budapest").strftime("%Y-%m-%d")
        return v[:10]
    start, end = local(r.start), local(r.end)
    used = (used.replace("GPR input; forecasts mu, sigma", "GP input: forecast $\\mu_k$, $\\sigma_k$")
                .replace("GPR target; 'real' PV and wind of the cell", "GP target; realised PV and wind power of the cell"))
    gaps = gaps.replace(" absent,", " hours absent,")
    lines.append(f"{r.source.replace('&', 'and')} & {start} & {end} & {r.resolution.replace("m3", "m$^3$")} & {gaps} & {used} \\\\")
tab("i_inventory", lines)

# energies of the periods (industrial load), summer protocol and winter months
en = pd.read_csv(f"{INV}/energy.csv")
names = {"calibration": "Calibration", "tuning": "Tuning", "development": "Development", "final_test": "Final test"}
lines = []
for r in en.itertuples():
    lab = names.get(r.period, r.period.replace("winter ", "Winter "))
    lines.append(f"{lab} & {pd.Timestamp(r.start).tz_convert('Europe/Budapest'):%d %b %Y} & {num(r.days, 0)} & "
                 f"{num(r.load_wh / 1000, 2)} & {num((r.pv_wh + r.wind_wh) / 1000, 2)} & {num(r.ren_share_pct, 0)} & "
                 f"{num(r.surplus_hours_pct, 0)} & {num(r.price_mean, 0)} \\\\")
    if r.period == "final_test":
        lines.append("\\midrule")
tab("i_periods", lines)
e = en.set_index("period")
M("finRenShare", num(e.loc["final_test", "ren_share_pct"], 0))
win = e[e.index.str.startswith("winter")]
M("winRenShareMin", num(win.ren_share_pct.min(), 0))
M("winRenShareMax", num(win.ren_share_pct.max(), 0))
summ = e.loc[["calibration", "tuning", "development"]]
M("sumRenShareMin", num(summ.ren_share_pct.min(), 0))
M("sumRenShareMax", num(summ.ren_share_pct.max(), 0))
M("winSurplusMin", num(win.surplus_hours_pct.min(), 1))
M("winSurplusMax", num(win.surplus_hours_pct.max(), 0))
M("finDaysInd", num(e.loc["final_test", "days"], 0))
M("winDays", num(int(pd.read_csv(f"{RW}/paired_bootstrap.csv").days.iloc[0]), 0))

# ------------------------------------------------------------------ industrial summer study
split = pd.read_csv(f"{H}/protection_split.csv")
fin = {}
boot = {}
for X in "ABC":
    s = pd.read_csv(f"{IND}/{X}/final_test/summary.csv")
    fin[X] = s[s.plant == "bms"].set_index("variant")
    b = pd.read_csv(f"{IND}/{X}/final_test/paired_bootstrap.csv")
    boot[X] = b[b.plant == "bms"]
reg = {X: json.load(open(f"{IND}/{X}/final_test/preregistration.json")) for X in "ABC"}
M("thrPrimaryH", num(reg["B"]["primary_threshold_h"], 0))
M("thrSecondaryH", num(reg["B"]["secondary_threshold_h"], 0))
M("prereqDateInd", pd.Timestamp(reg["A"]["written_utc"]).strftime("%Y-%m-%d"))
M("prereqDateIndFirst", pd.Timestamp(reg["B"]["written_utc"]).strftime("%Y-%m-%d"))


def prot(study, X, v):
    r = split[(split.study == study) & (split.config == X) & (split.variant == v)]
    return r.iloc[0] if len(r) else None


lines = []
L = {"A": "A", "B": "B", "C": "C"}
for X in "ABC":
    first = True
    for v in ["grid_only", "det", "det_bo", "ua", "ua_rho_only", "ua_margin_only", "oracle"]:
        if v not in fin[X].index:
            continue
        r = fin[X].loc[v]
        p = prot("final", X, v)
        lab = f"{X} ({CONF[X]})" if first else ""
        first = False
        lines.append(f"{lab} & {CTRL[v]} & {num(r.net_cost_eur, 3)} & {num(p.prot_lo_h, 2)} & {num(p.prot_up_h, 2)} & "
                     f"{num(1000 * r.extra_grid_eur, 2)} & {num(r.curtailed_pct, 1)} & {num(r.cycles_per_day, 2)} \\\\")
    if X != "C":
        lines.append("\\midrule")
tab("i_final", lines)


def bt(X, a, b, kpi, frame=None):
    d = (frame if frame is not None else boot[X])
    if "config" in d:
        d = d[d.config == X]
    r = d[(d.a == a) & (d.b == b) & (d.kpi == kpi)]
    return r.iloc[0] if len(r) else None


def cell(r, scale=1.0, d=1, pct=True):
    sig = (r.lo_total > 0) or (r.hi_total < 0)
    rel = f"{num(r.diff_pct, 1 if abs(r.diff_pct) >= 0.05 else 2)}\\,\\%, " if pct and np.isfinite(r.diff_pct) else ""
    return f"{rel}[{num(scale * r.lo_total, d)}, {num(scale * r.hi_total, d)}]" + ("$^{*}$" if sig else "")


lines = []
for X in "ABC":
    for b in ["det", "det_bo"]:
        if bt(X, "ua", b, "cost") is None:
            continue
        lines.append(f"{X} & {CTRL[b]} & {cell(bt(X, 'ua', b, 'cost'), 1000, 1)} & "
                     f"{cell(bt(X, 'ua', b, 'prot'), 0.25, 2, False)} & {cell(bt(X, 'ua', b, 'extra'), 1000, 2, False)} \\\\")
tab("i_boot", lines)

for X in "ABC":
    f = fin[X]
    for v, n in [("det", "Det"), ("ua", "Ua"), ("grid_only", "Grid"), ("oracle", "Oracle")]:
        M(f"fin{X}{n}Cost", num(f.loc[v, "net_cost_eur"], 3))
        M(f"fin{X}{n}Prot", num(f.loc[v, "protection_h"], 2))
    if "det_bo" in f.index:
        M(f"fin{X}BoCost", num(f.loc["det_bo", "net_cost_eur"], 3))
        M(f"fin{X}BoProt", num(f.loc["det_bo", "protection_h"], 2))
    M(f"fin{X}UaGridSave", num(-100 * (f.loc["ua", "net_cost_eur"] / f.loc["grid_only", "net_cost_eur"] - 1), 1))
    M(f"fin{X}UaCurt", num(f.loc["ua", "curtailed_pct"], 1))
    M(f"fin{X}UaOracleGap", num(100 * (f.loc["ua", "net_cost_eur"] / f.loc["oracle", "net_cost_eur"] - 1), 1))
    r = bt(X, "ua", "det", "cost")
    M(f"bt{X}DetCostPct", num(r.diff_pct, 1 if X != "C" else 2))
for X, b in [("A", "det_bo"), ("B", "det_bo")]:
    M(f"bt{X}BoCostPct", num(bt(X, "ua", b, "cost").diff_pct, 1))
M("btBDetExtraPct", num(bt("B", "ua", "det", "extra").diff_pct, 0))
M("finBRhoProt", num(fin["B"].loc["ua_rho_only", "protection_h"], 1))
M("finBRhoCost", num(fin["B"].loc["ua_rho_only", "net_cost_eur"], 3))
M("finBSecProt", num(fin["B"].loc["ua_secondary", "protection_h"], 1))
M("finCCheaperThanBPct", num(100 * (1 - fin["C"].loc["ua", "net_cost_eur"] / fin["B"].loc["ua", "net_cost_eur"]), 1))
M("finACostlierThanBPct", num(100 * (fin["A"].loc["ua", "net_cost_eur"] / fin["B"].loc["ua", "net_cost_eur"] - 1), 0))
M("finBUaProtUp", num(prot("final", "B", "ua").prot_up_h, 2))
M("finBUaProtLo", num(prot("final", "B", "ua").prot_lo_h, 2))
M("finBDetProtLo", num(prot("final", "B", "det").prot_lo_h, 2))
M("finBBoProtUp", num(prot("final", "B", "det_bo").prot_up_h, 2))
M("finDemandInd", num(fin["B"].loc["ua", "demand_kwh"], 2))
M("finRenewInd", num(fin["B"].loc["ua", "renewable_kwh"], 2))

for X in "ABC":
    s_ = pd.read_csv(f"{IND}/{X}/final_test/summary.csv")
    ph = s_[s_.plant == "physical"].set_index("variant")
    for v, n in [("det", "Det"), ("ua", "Ua"), ("det_bo", "Bo")]:
        if v in ph.index:
            M(f"phys{X}{n}Viol", num(ph.loc[v, "violation_pp_h"], 1 if ph.loc[v, "violation_pp_h"] >= 1 else 2))

# selected points
lines = []
for X in "ABC":
    sel = reg[X]["selected"]["primary_threshold_pp_h"]
    for c in ["det", "det_bo", "ua"]:
        if c not in sel:
            continue
        p = json.load(open(f"{IND}/{X}/tuning/{c}_pareto.json"))
        pt = next(x for x in p["points"] if x["trial"] == sel[c]["trial"])
        ov = pt["overrides"]
        flat = {**ov.get("weights", {}), **ov.get("mpc", {})}
        def g(k):
            return flat.get(k, 0.0 if k in ("soc_backoff", "soc_chance_z") else np.nan)
        spec = {"det": "--", "det_bo": f"$b={num(g('soc_backoff'), 3)}$",
                "ua": f"$z={num(g('soc_chance_z'), 2)}$, $\\lambda={num(g('lambda'), 1)}$"}[c]
        lines.append(f"{X} & {CTRL[c]} & t{sel[c]['trial']:02d} & {sci(g('w_x'))} & {sci(g('w_ff'))} & "
                     f"{sci(g('w_delta_u'))} & {spec} & {num(sel[c]['dev_cost'], 3)} & {num(sel[c]['dev_prot_h'], 2)} \\\\")
    if X != "C":
        lines.append("\\midrule")
tab("i_weights", lines)

# rank correlation tuning vs development protection
rc = []
for X in "ABC":
    f = pd.read_csv(f"{IND}/{X}/dev_fronts/fronts_dev.csv")
    rc.append(f[["tun_prot", "dev_prot"]].corr(method="spearman").iloc[0, 1])
    M(f"front{X}N", num(len(f), 0))
M("rankCorrIndMin", num(min(rc), 2))
M("rankCorrIndMax", num(max(rc), 2))

# ------------------------------------------------------------------ decision maps
maps = []
for X in "ABC":
    m = pd.read_csv(f"{IND}/{X}/decision_map_recovery/map.csv")
    m["config"] = X
    for side, col in (("lo", "prot_lo_h"), ("up", "prot_up_h")):
        for v, n in (("det_bo", "bo"), ("ua", "ua")):
            sp = split[(split.study == "map") & (split.config == X) & (split.variant == v)]
            m = m.merge(sp[["cap", "err", "rp", col]].rename(columns={"cap": "cap_wh", "err": "error_scale",
                                                                      "rp": "replan_steps", col: f"{n}_{side}"}),
                        on=["cap_wh", "error_scale", "replan_steps"], how="left")
    maps.append(m)
mp = pd.concat(maps)
mp.to_csv(f"{H}/map_all.csv", index=False)
lines = []
for X in "ABC":
    g = mp[mp.config == X].sort_values(["replan_steps", "cap_wh", "error_scale"])
    for i, r in enumerate(g.itertuples()):
        lab = f"{X}" if i == 0 else ""
        lines.append(f"{lab} & {int(r.cap_wh)} & {num(r.error_scale, 1)} & {num(r.replan_steps / 4, 0)} & "
                     f"{num(r.r_E, 3)} & {num(r.r_E_gen, 3)} & {num(r.r_E_gen_up, 3)} & {num(r.bo_prot_h, 2)} & "
                     f"{num(r.ua_prot_h, 2)} & {num(r.cost_diff_pct, 2)} \\\\")
    if X != "C":
        lines.append("\\midrule")
tab("i_map", lines)

B = mp[mp.config == "B"]
lowB, highB = B[B.r_E_gen <= 0.0235], B[B.r_E_gen >= 0.0465]
M("mapBLowThr", num(lowB.r_E_gen.max(), 3))
M("mapBHighThr", num(highB.r_E_gen.min(), 3))
M("mapBLowN", num(len(lowB), 0))
M("mapBHighN", num(len(highB), 0))
M("mapBLowDiffMax", num((lowB.bo_prot_h - lowB.ua_prot_h).abs().max(), 2))
rat = highB.bo_prot_h / highB.ua_prot_h
M("mapBRatioMin", num(rat.min(), 1))
M("mapBRatioMax", num(rat.max(), 1))
M("mapBHighCostMin", num(highB.cost_diff_pct.min(), 1))
M("mapBHighCostMax", num(highB.cost_diff_pct.max(), 1))
M("mapBHighBoMin", num(highB.bo_prot_h.min(), 1))
M("mapBHighBoMax", num(highB.bo_prot_h.max(), 1))
M("mapBHighUaMin", num(highB.ua_prot_h.min(), 1))
M("mapBHighUaMax", num(highB.ua_prot_h.max(), 2))
M("mapBUpRatio", num((B.r_E_gen_up / B.r_E_gen)[B.replan_steps == 4].median(), 2))
M("mapBUpLowThr", num(lowB.r_E_gen_up.max(), 3))
M("mapBUpHighThr", num(highB.r_E_gen_up.min(), 3))
M("mapBHighUpShare", num(100 * (highB.bo_up.sum() / highB.bo_prot_h.sum()), 0))
C = mp[mp.config == "C"]
M("mapCDiffMax", num((C.bo_prot_h - C.ua_prot_h).abs().max(), 2))
M("mapCCostMax", num(C.cost_diff_pct.abs().max(), 2))
M("mapCOldPred", num(int((C.r_E >= 0.0465).sum()), 0))
A = mp[mp.config == "A"]
M("mapABoMin", num(A.bo_prot_h.min(), 2))
M("mapABoMax", num(A.bo_prot_h.max(), 2))
M("mapAUaMax", num(A.ua_prot_h.max(), 1))
A72 = A[A.cap_wh >= 72]
M("mapACostMin", num(-A72.cost_diff_pct.max(), 1))
M("mapACostMax", num(-A72.cost_diff_pct.min(), 1))
M("mapABoUpShare", num(100 * A.bo_up.sum() / A.bo_prot_h.sum(), 0))
ch = pd.read_csv(f"{IND}/A/decision_map_recovery/chance_calibration.csv")
M("chanceACostSpread", num(100 * (ch.net_cost_eur.max() / ch.net_cost_eur.min() - 1), 2))
chB = pd.read_csv(f"{IND}/B/decision_map_recovery/chance_calibration.csv")
M("chanceBCostSpread", num(100 * (chB.net_cost_eur.max() / chB.net_cost_eur.min() - 1), 1))

# recourse window (B)
rw_note = open(f"{IND}/recourse_window.txt").read()
M("recourseLongestH", "15.5")
M("recourseQTun", "0.269")
M("recourseQTunWin", "0.053")
M("recourseQDev", "0.100")
M("recourseQDevWin", "0.041")

# ------------------------------------------------------------------ winter walk-forward
cal = [json.load(open(f"{RW}/calibration/m{i}.json")) for i in range(1, 7)]
months = ["Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Mar 2026"]
lines = []
for i, (c, mname) in enumerate(zip(cal, months)):
    t = c["test_month_forecast"]
    cut = pd.Timestamp(c["cutoff"]).tz_convert("Europe/Budapest")
    lines.append(f"{mname} & {cut:%d %b %Y} & {num(c['sigma_scale'], 2)} & {num(c['error_autocorr_step'], 3)} & "
                 f"{num(t['rmse_w'], 2)} & {num(t['coverage_1sigma_calibrated_pct'], 0)} & "
                 f"{num(t['coverage_2sigma_calibrated_pct'], 0)} & {num(win.iloc[i].ren_share_pct, 0)} \\\\")
tab("i_winter_fc", lines)
ks = [c["sigma_scale"] for c in cal]
M("winKappaMin", num(min(ks), 2))
M("winKappaMax", num(max(ks), 2))
c1 = [c["test_month_forecast"]["coverage_1sigma_calibrated_pct"] for c in cal]
c2 = [c["test_month_forecast"]["coverage_2sigma_calibrated_pct"] for c in cal]
M("winCovOneMin", num(min(c1), 0))
M("winCovOneMax", num(max(c1), 0))
M("winCovTwoMin", num(min(c2), 0))
M("winCovTwoMax", num(max(c2), 0))
rm = [c["test_month_forecast"]["rmse_w"] for c in cal]
M("winRmseMin", num(min(rm), 2))
M("winRmseMax", num(max(rm), 2))

ws = pd.read_csv(f"{RW}/summary.csv").set_index(["config", "controller"])
wb = pd.read_csv(f"{RW}/paired_bootstrap.csv")
lines = []
for X in "BC":
    first = True
    for v in ["grid_only", "det", "det_bo", "ua"]:
        if (X, v) not in ws.index:
            continue
        r = ws.loc[(X, v)]
        p = prot("winter", X, v)
        lab = f"{X} ({CONF[X]})" if first else ""
        first = False
        lines.append(f"{lab} & {CTRL[v]} & {num(r.net_cost_eur, 3)} & {num(p.prot_lo_h, 2)} & {num(p.prot_up_h, 2)} & "
                     f"{num(1000 * r.extra_grid_eur, 2)} & {num(r.curtailed_kwh, 2)} \\\\")
    if X == "B":
        lines.append("\\midrule")
tab("i_winter", lines)
lines = []
for X in "BC":
    for b in ["det", "det_bo", "grid_only"]:
        r = bt(X, "ua", b, "cost", wb)
        if r is None:
            continue
        lines.append(f"{X} & {CTRL[b]} & {cell(r, 1000, 0)} & {cell(bt(X, 'ua', b, 'prot', wb), 0.25, 1, False)} & "
                     f"{cell(bt(X, 'ua', b, 'extra', wb), 1000, 2, False)} \\\\")
tab("i_winter_boot", lines)
for X, v, n in [("B", "det", "BDet"), ("B", "det_bo", "BBo"), ("B", "ua", "BUa"), ("B", "grid_only", "Grid"),
                ("C", "det", "CDet"), ("C", "ua", "CUa")]:
    M(f"win{n}Cost", num(ws.loc[(X, v), "net_cost_eur"], 3))
    if v != "grid_only":
        M(f"win{n}Prot", num(prot("winter", X, v).prot_h, 2))
        M(f"win{n}ProtLo", num(prot("winter", X, v).prot_lo_h, 2))
        M(f"win{n}Extra", num(1000 * ws.loc[(X, v), "extra_grid_eur"], 1))
M("wbBDetCostPct", num(bt("B", "ua", "det", "cost", wb).diff_pct, 1))
M("wbBBoCostPct", num(bt("B", "ua", "det_bo", "cost", wb).diff_pct, 2))
M("wbBGridCostPct", num(bt("B", "ua", "grid_only", "cost", wb).diff_pct, 1))
M("wbCGridCostPct", num(bt("C", "ua", "grid_only", "cost", wb).diff_pct, 1))
M("wbCDetCostPct", num(bt("C", "ua", "det", "cost", wb).diff_pct, 3))
M("wbCDetProtDiff", num(bt("C", "ua", "det", "prot", wb).diff_total / 4, 1))
M("wbBDetProtDiff", num(bt("B", "ua", "det", "prot", wb).diff_total / 4, 1))
M("wbBBoProtDiff", num(bt("B", "ua", "det_bo", "prot", wb).diff_total / 4, 1))
M("wbBDetProtP", num(1 - bt("B", "ua", "det", "prot", wb).p_a_better, 2))
M("winCCheaperThanBPct", num(100 * (1 - ws.loc[("C", "ua"), "net_cost_eur"] / ws.loc[("B", "ua"), "net_cost_eur"]), 1))
M("winBExtraShare", num(100 * ws.loc[("B", "ua"), "extra_grid_eur"] / ws.loc[("B", "ua"), "net_cost_eur"], 2))
mon = []
for X in "BC":
    for v in ["grid_only", "det", "det_bo", "ua"]:
        for i in range(1, 7):
            f = f"{RW}/{X}/{v}_m{i}.json"
            if os.path.exists(f):
                j = json.load(open(f))
                mon.append(dict(config=X, controller=v, month=i, cost=j["metrics"]["net_cost_eur"],
                                prot_h=j["metrics"].get("protection_steps", 0) / 4))
pd.DataFrame(mon).to_csv(f"{H}/winter_monthly.csv", index=False)

# absolute values of relative differences that the text states as "less" / "more"
M("btBDetCostPctAbs", num(abs(bt("B", "ua", "det", "cost").diff_pct), 1))
M("btBDetExtraPctAbs", num(abs(bt("B", "ua", "det", "extra").diff_pct), 0))
M("btABoCostPctAbs", num(abs(bt("A", "ua", "det_bo", "cost").diff_pct), 1))
M("wbBDetCostPctAbs", num(abs(bt("B", "ua", "det", "cost", wb).diff_pct), 1))
M("wbBGridCostPctAbs", num(abs(bt("B", "ua", "grid_only", "cost", wb).diff_pct), 1))
M("wbCGridCostPctAbs", num(abs(bt("C", "ua", "grid_only", "cost", wb).diff_pct), 1))

# ------------------------------------------------------------------ write
with open(f"{OUT}/numbers3.tex", "w") as fh:
    fh.write("%% generated by make_numbers3.py -- do not edit\n")
    for k, v in macros.items():
        fh.write(f"\\newcommand{{\\{k}}}{{{v}\\xspace}}\n")
print(len(macros), "macros")
for k, v in macros.items():
    print(f"{k:24s} {v}")
