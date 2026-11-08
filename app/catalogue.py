"""What the paper reports, where, and how each value is recomputed from the result files.

Every entry holds the value exactly as printed in the paper. A value counts as reproduced when the recomputed
number, rounded to the decimals printed in the paper, equals it. Ranges ("a–b") are checked at both ends,
counts ("28 of 33") exactly.
"""
from __future__ import annotations

import glob
import inspect
import json
import re
from functools import cache

import pandas as pd

from app.pipeline import OUT, RESULTS

EXT = RESULTS / "extended"

# ---------------------------------------------------------------- figures (paper number, file in out/figures)
FIGURES = [
    ("Fig. 1", "Data coverage and experiment periods", "fig01_coverage"),
    ("Fig. 2", "Load profile of the plant", "fig02_load_profile"),
    ("Fig. 3", "Monthly energy of the scaled cell", "fig03_monthly_energy"),
    ("Fig. 4", "Battery configurations", "fig04_configurations"),
    ("Fig. 5", "Architecture of the scaled replay", "fig00_architecture"),
    ("Fig. 6", "Final test: net cost", "fig05_final_cost"),
    ("Fig. 7", "Final test: protection hours by SoC edge", "fig06_final_protection"),
    ("Fig. 8", "Decision map, configuration C", "fig09_map_C"),
    ("Fig. 9", "Decision map, configuration B", "fig08_map_B"),
    ("Fig. 10", "Protection difference against Γ", "fig16_gamma_col"),
    ("Fig. 11", "Winter test: monthly saving", "fig10_winter_saving"),
    ("Fig. 12", "Winter test: monthly protection, B", "fig11_winter_protection"),
    ("Fig. 13", "UA-MPC minus reference: net cost", "fig15a_summary_cost"),
    ("Fig. 14", "UA-MPC minus reference: protection", "fig15b_summary_protection"),
    ("Fig. 15", "Decision guide", "fig17_decision_guide"),
    ("Fig. S1", "Second load case: decision map", "fig12_case2_map"),
    ("Fig. S2", "Second load case: variability experiment", "fig13_case2_variability"),
    ("Fig. S3", "Second load case: capacity", "fig14_case2_capacity"),
    ("GA", "Graphical abstract", "graphical_abstract"),
]

# ---------------------------------------------------------------- helpers reading the result files
@cache
def macros() -> dict[str, float]:
    """Numbers computed by experiments/analysis/make_numbers.py (out/manuscript/numbers3.tex)."""
    t = (OUT / "manuscript" / "numbers3.tex").read_text()
    out = {}
    for name, val in re.findall(r"\\newcommand\{\\(\w+)\}\{(.*?)\\xspace\}", t):
        v = val.replace("{,}", "").replace("$-$", "-")
        try:
            out[name] = float(v)
        except ValueError:
            pass
    return out


def m(name: str):
    f = lambda: macros()[name]  # noqa: E731
    f.macros = [name]
    return f


def csv(rel: str) -> pd.DataFrame:
    return pd.read_csv(EXT / rel)


def jload(path) -> dict:
    return json.load(open(path))


def eff_gamma(col: str, rows: str = "winter"):
    def f():
        d = csv("ratio/effective_gamma.csv"); d = d[d.period.str.startswith(rows)]
        return (d[col].min(), d[col].max())
    return f


def calib(col: str, winter: bool):
    def f():
        d = csv("calibration/calib_diag.csv"); w = d.period.str.startswith("winter")
        d = d[w] if winter else d[~w]
        return (d[col].min(), d[col].max())
    return f


def t1_totals():
    c = p = 0.0
    for k in range(2, 7):
        mm = jload(EXT / f"preregistered/t1/det_bo_sized_m{k}.json")["metrics"]; c += mm["net_cost_eur"]; p += mm["protection_h"]
    return c, p


def winter_ref(ctrl: str, months=range(2, 7)):
    """Net cost and protection of a summer-selected controller in winter B, months m2..m6 (Nov–Mar)."""
    c = p = 0.0
    for k in months:
        f = glob.glob(str(RESULTS / f"winter/B/{ctrl}_m{k}.json")) or glob.glob(str(RESULTS / f"winter/B/*/{ctrl}_m{k}.json"))
        mm = jload(f[0])["metrics"]; c += mm["net_cost_eur"]; p += mm["protection_h"]
    return c, p


def t1_gamma():
    oos = csv("ratio/rE_outofsample.csv"); oos = oos[oos.config == "B"].set_index("period")
    g = []
    for k in range(2, 7):
        b = jload(EXT / f"preregistered/t1/det_bo_sized_m{k}.json")["b"]
        g.append(oos.loc[f"winter m{k}", "r_E"] * 0.8 / b)
    return min(g), max(g)


def prereg_counts():
    """Firm predictions from Γ in the three pre-registered experiments (P1, P3) and how many held."""
    d = pd.read_csv(OUT / "work" / "rev3" / "figs" / "fig16_data.csv") if (OUT / "work/rev3/figs/fig16_data.csv").exists() \
        else csv("preregistered/figs/fig16_data.csv")
    d = d[d.set != "Decision map (development)"]
    sweep = d.set.str.startswith("Back-off")
    pred = pd.Series(index=d.index, dtype=object)
    pred[~sweep] = ["tie" if g < 1 else "difference" for g in d.Gamma[~sweep]]
    pred[sweep] = ["tie" if g <= 1 + 1e-9 else ("difference" if g >= 1.5 - 1e-9 else None) for g in d.Gamma[sweep]]
    firm = pred.notna()
    held = firm & (pred == d.label)
    ties = firm & (pred == "tie"); diffs = firm & (pred == "difference")
    return {"firm": int(firm.sum()), "held": int(held.sum()), "ties": int(ties.sum()), "ties_held": int((ties & held).sum()),
            "diffs": int(diffs.sum()), "diffs_held": int((diffs & held).sum())}


def t2_counts():
    """T2: back-off sweep of the second load case, label rule +1 pp·h and twice the UA-MPC violation."""
    held = n = 0
    for f in glob.glob(str(EXT / "preregistered/t2/steps/bsw2_det_bo_*.json")):
        if f.endswith("_solves.json"):
            continue
        cell, g = re.search(r"bsw2_det_bo_(cap\d+_err[\d.]+_rp\d+)_G([\d.]+)\.json", f).groups(); g = float(g)
        ua = jload(RESULTS / f"production_schedule/decision_map_recovery/steps/ua_{cell}.json")["metrics"]["violation_pp_h"]
        bo = jload(f)["metrics"]["violation_pp_h"]
        label = "difference" if (bo >= ua + 1.0 and bo >= 2 * ua) else "tie"
        pred = "tie" if g <= 1 else ("difference" if g >= 1.5 else None)
        if pred:
            n += 1; held += pred == label
    return held, n


def indicator(name: str, col: str):
    return lambda: csv("indicators/indicator_benchmark.csv").set_index("indicator").loc[name, col]


def expost(config: str, ctrl: str, col: str):
    def f():
        d = csv("tariffs/expost.csv"); return d[(d.test == "summer") & (d.config == config) & (d.ctrl == ctrl)][col].iloc[0]
    return f


def sens(config: str, ctrl: str, scen: str, col: str):
    def f():
        d = csv("sensitivity/sens.csv"); return d[(d.config == config) & (d.ctrl == ctrl) & (d.scenario == scen)][col].iloc[0]
    return f


def gcheck(*keys):
    """experiments/extended/ratio/gamma_windows.py: persistence of r_E, Γ against protection, window-by-window check."""
    def f():
        d = jload(EXT / "ratio/gamma_check.json")
        for k in keys[:-1]:
            d = d[k]
        return d[keys[-1]] if not isinstance(keys[-1], tuple) else tuple(d[x] for x in keys[-1])
    return f


def scen(*cols):
    """experiments/extended/ratio/scenario_robust.py: min and max over the summer periods and the T1 winter months."""
    def f():
        d = csv("ratio/scenario_robust.csv")
        v = pd.concat([d[c] for c in cols])
        return v.min(), v.max()
    return f


def t3(rule: str, key: str = "protection_h"):
    """T3 (results/extended/preregistered/t3): Nov–Mar total of the scenario or robust back-off."""
    return lambda: sum(jload(EXT / f"preregistered/t3/det_bo_{rule}_m{k}.json")["metrics"][key] for k in range(2, 7))


def t3_final(rule: str, key: str = "protection_h"):
    return lambda: jload(EXT / f"preregistered/t3/steps/det_bo_{rule}_final.json")["metrics"][key]


def seasonal_ua():
    c = p = 0.0
    for k in range(3, 7):
        mm = jload(EXT / f"seasonal/test/ua_m{k}.json")["metrics"]; c += mm["net_cost_eur"]; p += mm["protection_h"]
    return c, p


# ---------------------------------------------------------------- claims: (section, statement, value as printed, compute)
# compute returns a number, a (min, max) pair for a printed range, or (held, total) for "x of y".
CLAIMS = [
    # 3 System and data
    ("3.2 Data", "Filled hours of the plant load (%)", "0.77", m("plantFilledPct")),
    ("3.2 Data", "Weekday 06:00–14:00 median load / annual mean", "2.4", m("plantShiftPu")),
    ("3.2 Data", "Winter / summer mean load per unit", "1.20 / 0.88", lambda: (macros()["plantWinterPu"], macros()["plantSummerPu"])),
    ("3.1 Cell", "Battery capacity in hours of mean load", "6.7", m("cellEpu")),
    ("3.2 Periods", "Renewable share of the load before the final test (%)", "61–89", lambda: (macros()["sumRenShareMin"], macros()["sumRenShareMax"])),
    ("3.2 Periods", "Renewable share in the final test (%)", "31", m("finRenShare")),
    ("3.2 Periods", "Renewable share in the winter months (%)", "10–29", lambda: (macros()["winRenShareMin"], macros()["winRenShareMax"])),
    # 5.1 Calibration and tuning
    ("5.1 Calibration", "Pareto front points of A, B, C", "42 / 40 / 29", lambda: (macros()["frontAN"], macros()["frontBN"], macros()["frontCN"])),
    ("5.1 Calibration", "Spearman, tuning vs development protection", "0.82–0.91", lambda: (macros()["rankCorrIndMin"], macros()["rankCorrIndMax"])),
    # 5.2 Final test
    ("5.2 Final test", "UA-MPC saving vs grid only, A / B / C (%)", "21.6 / 20.1 / 20.6",
     lambda: (macros()["finAUaGridSave"], macros()["finBUaGridSave"], macros()["finCUaGridSave"])),
    ("5.2 Final test", "A more expensive than B and C (%)", "21", m("finACostlierThanBPct")),
    ("5.2 Final test", "C cheaper than B, UA-MPC (%)", "0.6", m("finCCheaperThanBPct")),
    ("5.2 Final test", "Curtailed share, C vs B (%)", "8.8 / 6.8", lambda: (macros()["finCUaCurt"], macros()["finBUaCurt"])),
    ("5.2 Final test", "C: net cost DET / UA-MPC (€)", "0.778 / 0.778", lambda: (macros()["finCDetCost"], macros()["finCUaCost"])),
    ("5.2 Final test", "C: protection DET / UA-MPC / oracle (h)", "5.50 / 6.00 / 6.25",
     lambda: (macros()["finCDetProt"], macros()["finCUaProt"], macros()["finCOracleProt"])),
    ("5.2 Final test", "B: UA-MPC cheaper than DET (%)", "3.3", m("btBDetCostPctAbs")),
    ("5.2 Final test", "B: net cost UA-MPC / DET (€)", "0.783 / 0.809", lambda: (macros()["finBUaCost"], macros()["finBDetCost"])),
    ("5.2 Final test", "B: extra grid cost of stopped discharge reduced by (%)", "97", m("btBDetExtraPctAbs")),
    ("5.2 Final test", "B: protection UA-MPC / DET (h)", "9.50 / 11.25", lambda: (macros()["finBUaProt"], macros()["finBDetProt"])),
    ("5.2 Final test", "B: lower edge DET / UA-MPC (h)", "7.50 / 0.25", lambda: (macros()["finBDetProtLo"], macros()["finBUaProtLo"])),
    ("5.2 Final test", "B: upper edge UA-MPC / DET-BO (h)", "9.25 / 9.75", lambda: (macros()["finBUaProtUp"], macros()["finBBoProtUp"])),
    ("5.2 Final test", "B: protection without the margin (ρ only) (h)", "42.5", m("finBRhoProt")),
    ("5.2 Final test", "B: point under the 10-h threshold (h)", "22.2", m("finBSecProt")),
    ("5.2 Final test", "A: protection DET / DET-BO / UA-MPC (h)", "2.50 / 0.75 / 0.00",
     lambda: (macros()["finADetProt"], macros()["finABoProt"], macros()["finAUaProt"])),
    ("5.2 Final test", "A: UA-MPC vs DET / vs DET-BO (%)", "3.7 / 5.7", lambda: (macros()["btADetCostPct"], macros()["btABoCostPctAbs"])),
    ("5.2 Final test", "B, physical plant: UA-MPC / DET-BO / DET (pp·h)", "100.4 / 123.2 / 17.7",
     lambda: (macros()["physBUaViol"], macros()["physBBoViol"], macros()["physBDetViol"])),
    # 5.3 Decision map
    ("5.3 Decision map", "Bounds on r_E: tie / difference", "0.023 / 0.047", lambda: (macros()["mapBLowThr"], macros()["mapBHighThr"])),
    ("5.3 Decision map", "B: cells below / above the bounds", "6 / 4", lambda: (macros()["mapBLowN"], macros()["mapBHighN"])),
    ("5.3 Decision map", "B: tie cells at most apart (h)", "2.25", m("mapBLowDiffMax")),
    ("5.3 Decision map", "B: DET-BO protection in difference cells (h)", "7.2–38.2", lambda: (macros()["mapBHighBoMin"], macros()["mapBHighBoMax"])),
    ("5.3 Decision map", "B: UA-MPC protection in difference cells (h)", "1.2–12.00", lambda: (macros()["mapBHighUaMin"], macros()["mapBHighUaMax"])),
    ("5.3 Decision map", "B: times fewer protection hours", "2.4–5.8", lambda: (macros()["mapBRatioMin"], macros()["mapBRatioMax"])),
    ("5.3 Decision map", "B: extra cost of UA-MPC in difference cells (%)", "0.7–2.6", lambda: (macros()["mapBHighCostMin"], macros()["mapBHighCostMax"])),
    ("5.3 Decision map", "C: largest protection difference (h)", "2.00", m("mapCDiffMax")),
    ("5.3 Decision map", "Surplus ratio / deficit ratio", "1.75", m("mapBUpRatio")),
    ("5.3 Decision map", "AUC: r_E / 24-h window / sizing / mean σ", "1.00 / 0.92 / 0.86 / 0.74",
     lambda: (indicator("r_E", "auc_30")(), indicator("r_E_day", "auc_30")(), indicator("S24", "auc_30")(), indicator("sigma_mean", "auc_30")())),
    ("5.3 Decision map", "Leave-one-family-out: r_E / 24-h error / mean σ", "30 / 25 / 15",
     lambda: (indicator("r_E", "lofo")(), indicator("S24", "lofo")(), indicator("sigma_mean", "lofo")())),
    ("5.3 Decision map", "r_E in the final test of A and B", "0.035", lambda: csv("ratio/effective_gamma.csv").set_index("period").loc["final_test", "r_E"]),
    ("5.3 Decision map", "r_E in the winter months, B", "0.049–0.087", eff_gamma("r_E")),
    # 5.4 Pre-registered tests
    ("5.4 Pre-registered", "Firm predictions from Γ that held", "28 of 33", lambda: (prereg_counts()["held"], prereg_counts()["firm"])),
    ("5.4 Pre-registered", "Ties that held", "15 of 18", lambda: (prereg_counts()["ties_held"], prereg_counts()["ties"])),
    ("5.4 Pre-registered", "Differences that held", "13 of 15", lambda: (prereg_counts()["diffs_held"], prereg_counts()["diffs"])),
    ("5.4 Pre-registered", "T2: predictions that held", "18 of 20", t2_counts),
    ("5.4 Pre-registered", "T1: re-sized back-off, Nov–Mar (€ / h)", "4.815 / 7.50", t1_totals),
    ("5.4 Pre-registered", "T1: summer-tuned DET-BO, Nov–Mar (€ / h)", "4.786 / 32.25", lambda: winter_ref("det_bo")),
    ("5.4 Pre-registered", "T1: UA-MPC, Nov–Mar (€ / h)", "4.782 / 39.50", lambda: winter_ref("ua")),
    ("5.4 Pre-registered", "T1: extra cost of the re-sized back-off vs UA-MPC (%)", "0.68",
     lambda: 100 * (t1_totals()[0] / winter_ref("ua")[0] - 1)),
    ("5.4 Pre-registered", "T1: realised Γ of the re-sized back-off", "0.59–1.03", t1_gamma),
    ("5.4 Pre-registered", "T1: back-off of the summer final test", "0.028",
     lambda: jload(EXT / "preregistered/t1/steps/det_bo_sized_final.json")["b"]),
    # 5.5 Winter
    ("5.5 Winter", "Calibration scale κ in winter", "0.63–0.99", lambda: (macros()["winKappaMin"], macros()["winKappaMax"])),
    ("5.5 Winter", "95 % quantile of the standardised window error, summer", "0.99–1.37", calib("zW_q95", False)),
    ("5.5 Winter", "95 % quantile of the standardised window error, winter", "1.40–2.86", calib("zW_q95", True)),
    ("5.5 Winter", "Saving vs grid only, B (UA-MPC) / C (%)", "9.3 / 10.0", lambda: (macros()["wbBGridCostPctAbs"], macros()["wbCGridCostPctAbs"])),
    ("5.5 Winter", "B: UA-MPC cheaper than DET (%)", "2.9", m("wbBDetCostPctAbs")),
    ("5.5 Winter", "B: protection UA-MPC / DET-BO / DET (h)", "46.50 / 38.75 / 24.75",
     lambda: (macros()["winBUaProt"], macros()["winBBoProt"], macros()["winBDetProt"])),
    ("5.5 Winter", "B: UA-MPC protection at the lower edge (h)", "40.50", m("winBUaProtLo")),
    ("5.5 Winter", "B: bootstrap share in favour of DET", "0.94", m("wbBDetProtP")),
    ("5.5 Winter", "B: stopped discharge, UA-MPC (m€ / % of net cost)", "6.7 / 0.12", lambda: (macros()["winBUaExtra"], macros()["winBExtraShare"])),
    ("5.5 Winter", "C: net cost UA-MPC / DET (€)", "5.384 / 5.383", lambda: (macros()["winCUaCost"], macros()["winCDetCost"])),
    ("5.5 Winter", "C: protection UA-MPC / DET (h)", "27.00 / 28.50", lambda: (macros()["winCUaProt"], macros()["winCDetProt"])),
    ("5.5 Winter", "C cheaper than B with UA-MPC (%)", "0.8", m("winCCheaperThanBPct")),
    ("5.5 Winter", "Γ of the UA-MPC margin, development", "0.62", lambda: csv("ratio/effective_gamma.csv").set_index("period").loc["development", "Gamma_UA"]),
    ("5.5 Winter", "Γ of the UA-MPC margin, winter", "0.91–2.00", eff_gamma("Gamma_UA")),
    ("5.5 Winter", "Γ of the summer-tuned back-off, winter", "1.30–2.31", eff_gamma("Gamma_BO")),
    ("5.5 Winter", "Seasonal re-selection: UA-MPC, Dec–Mar (€ / h)", "3.874 / 37.75", seasonal_ua),
    ("5.5 Winter", "Summer-selected UA-MPC, Dec–Mar (€ / h)", "3.875 / 33.50", lambda: winter_ref("ua", range(3, 7))),
    # S2 Sensitivity and tariffs
    ("S2 Sensitivity", "B, +10 % renewable power: protection DET-BO / UA-MPC (h)", "16.75 / 17.00",
     lambda: (sens("B", "det_bo", "renp10", "prot")(), sens("B", "ua", "renp10", "prot")())),
    ("S2 Tariffs", "Export: UA-MPC net cost C / B (€)", "0.753 / 0.774", lambda: (expost("C", "ua", "export")(), expost("B", "ua", "export")())),
    ("S2 Tariffs", "Demand charge: UA-MPC net cost B / C (€)", "1.079 / 1.090", lambda: (expost("B", "ua", "demand")(), expost("C", "ua", "demand")())),
]


# ---------------------------------------------------------------- further values of the text, found by going through it number by number
SUMMER = RESULTS / "summer"


def final_metric(X, v, key):
    return lambda: jload(SUMMER / X / f"final_test/steps/bms_{v}.json")["metrics"][key]


def solver_detail():
    st = pd.read_csv(SUMMER / "B/final_test/steps/bms_ua.csv"); st.index = pd.to_datetime(st.t, utc=True)
    sv = pd.read_csv(SUMMER / "B/final_test/steps/bms_ua_solves.csv"); sv.index = pd.to_datetime(sv.t, utc=True)
    ok = sv.success.reindex(st.index, method="ffill").fillna(True).astype(bool)
    prot = (st.cut_discharge_w > 1e-3) | (st.cut_charge_w > 1e-3)
    at_top = (~sv.success) & (st.soc.reindex(sv.index) >= 0.9 - 1e-6)
    return {"prot": int(prot.sum()), "prot_failed": int((prot & ~ok).sum()), "failed": int((~sv.success).sum()), "failed_top": int(at_top.sum())}


def winter_solver_min():
    """Share of converged solves per controller over the six winter months (as in Table 12), the lowest of them."""
    v = []
    for X in "BC":
        for c in ("det", "det_bo", "ua"):
            ms = [jload(f)["metrics"] for f in glob.glob(str(RESULTS / f"winter/{X}/{c}_m*.json"))]
            if ms:
                v.append(sum(x["solver_success_pct"] * x["solves"] for x in ms) / sum(x["solves"] for x in ms))
    return min(v)


def strict_solver():
    d = csv("sensitivity/sens.csv"); b = d[d.scenario == "base"].set_index(["config", "ctrl"]); s = d[d.scenario == "solver"].set_index(["config", "ctrl"])
    return (abs(s.prot - b.prot).max(), (100 * abs(s.cost / b.cost - 1)).max())


def chan_effect():
    d = csv("sensitivity/sens.csv"); u = d[d.ctrl == "ua"]
    b = u[u.scenario == "base"].set_index("config"); c = u[u.scenario == "chan"].set_index("config")
    return ((100 * abs(c.cost / b.cost.loc[c.index] - 1)).max(), abs(c.prot - b.prot.loc[c.index]).max())


def calch(per, ch, col):
    return lambda: pd.read_csv(RESULTS / "analysis/extended/calib_channels.csv").set_index(["period", "channel"]).loc[(per, ch), col]


def calcond(per, ch, group, scale, col):
    def f():
        d = csv("calibration/calib_conditional.csv")
        return d[(d.period == per) & (d.channel == ch) & (d.group == group) & (d.scale == scale)][col].iloc[0]
    return f


def kappa_ch(ch):
    return lambda: jload(EXT / "calibration/kappa_channel.json")["kappa_channel"][ch]


def rq_bounds(q):
    """Largest r_E of a tie and smallest of a difference in B for quantile q (results/analysis/extended/rE_quantiles.csv)."""
    def f():
        d = pd.read_csv(RESULTS / "analysis/extended/rE_quantiles.csv"); d = d[d.config == "B"]
        diff = (d.bo >= d.ua + 4) & (d.bo >= 2 * d.ua)
        return d[~diff][q].max(), d[diff][q].min()
    return f


def map_A_rmax():
    d = pd.read_csv(RESULTS / "analysis/extended/rE_quantiles.csv"); return d[d.config == "A"].q95.max()


def fig16(set_prefix):
    d = csv("preregistered/figs/fig16_data.csv"); return d[d.set.str.startswith(set_prefix)]


def misses():
    d = csv("preregistered/figs/fig16_data.csv"); d = d[d.set != "Decision map (development)"]
    sweep = d.set.str.startswith("Back-off")
    pred = pd.Series([("tie" if g <= 1 + 1e-9 else ("difference" if g >= 1.5 - 1e-9 else None)) if s else ("tie" if g < 1 else "difference")
                      for g, s in zip(d.Gamma, sweep)], index=d.index)
    return d[pred.notna() & (pred != d.label)]


def corr_cell(phi, col):
    def f():
        d = csv("indicators/ind_corr.csv"); return d[(d.cap == 108) & (d.phi == phi)][col].iloc[0]
    return f


def sweep_stats(G):
    def f():
        reg = jload(EXT / "preregistered/preregistration_rE.json"); fewer, cost = [], []
        for c in reg["b_sweep"]:
            if abs(c["Gamma"] - G) > 1e-9:
                continue
            a = jload(EXT / f"preregistered/runs_rE/steps/bsw_det_bo_cap{c['cap']}_err{c['err']}_rp{c['rp']}_G{c['Gamma']}.json")["metrics"]
            u = jload(SUMMER / f"B/decision_map_recovery/steps/ua_cap{c['cap']}_err{c['err']}_rp{c['rp']}.json")["metrics"]
            fewer.append(u["protection_h"] - a["protection_h"]); cost.append(100 * (a["net_cost_eur"] / u["net_cost_eur"] - 1))
        return fewer, cost
    return f


@cache
def t1_boot():
    """Paired block bootstrap of the daily costs, Nov–Mar: UA-MPC minus re-sized back-off and minus summer-tuned DET-BO."""
    import os
    import sys
    os.environ.setdefault("KAIROS_ROOT", str(RESULTS.parent / "code"))
    sys.path.insert(0, str(RESULTS.parent / "experiments/summer"))
    import engine
    def chain(fmt):
        return pd.concat([engine.daily(fmt.format(m=m)) for m in range(2, 7)])
    ua = chain(str(RESULTS / "winter/B/ua_m{m}.csv"))
    t1 = chain(str(EXT / "preregistered/t1/det_bo_sized_m{m}.csv"))
    bo = chain(str(RESULTS / "winter/B/det_bo_m{m}.csv"))
    return engine.paired(ua, t1, ["cost"])[0], engine.paired(ua, bo, ["cost"])[0]


def t1_monthly_extra_max():
    out = []
    for k in range(2, 7):
        a = jload(EXT / f"preregistered/t1/det_bo_sized_m{k}.json")["metrics"]["net_cost_eur"]
        u = jload(RESULTS / f"winter/B/ua_m{k}.json")["metrics"]["net_cost_eur"]; out.append(100 * (a / u - 1))
    return max(out)


def eg(period, col):
    return lambda: csv("ratio/effective_gamma.csv").set_index("period").loc[period, col]


def reselect(ctrl, key):
    return lambda: jload(EXT / "seasonal/reselection.json")["selected"][ctrl][key]


CLAIMS += [
    ("5.2 Final test", "Solves of UA-MPC in B that converged (%) / solves", "97.1 / 654",
     lambda: (final_metric("B", "ua", "solver_success_pct")(), final_metric("B", "ua", "solves")())),
    ("5.2 Final test", "Converged solves of DET-BO in B (%)", "99.4", final_metric("B", "det_bo", "solver_success_pct")),
    ("5.2 Final test", "Converged solves in winter, at least (%)", "99.8", winter_solver_min),
    ("5.2 Final test", "UA-MPC protection steps under a non-converged plan", "37 of 38",
     lambda: (solver_detail()["prot_failed"], solver_detail()["prot"])),
    ("5.2 Final test", "Failed solves that started at the upper edge", "10 of 19", lambda: (solver_detail()["failed_top"], solver_detail()["failed"])),
    ("5.2 Final test", "Stricter solver: protection within (h) / cost within (%)", "0.5 / 0.1", strict_solver),
    ("5.2 Final test", "A and C, physical plant: largest band violation (pp·h)", "0.4",
     lambda: max(macros()[k] for k in ("physADetViol", "physAUaViol", "physABoViol", "physCDetViol", "physCUaViol"))),
    ("5.2 Final test", "Final test: load / renewable energy (kWh)", "6.82 / 2.11", lambda: (macros()["finDemandInd"], macros()["finRenewInd"])),
    ("5.1 Calibration", "Solar ±1σ coverage, development / final test (%)", "94–77",
     lambda: (calch("development", "solar", "cov1")(), calch("final_test", "solar", "cov1")())),
    ("5.1 Calibration", "Wind ±2σ coverage on the largest-error quarter (%)", "80–83",
     lambda: (calch("development", "wind", "cov2_largest_quarter")(), calch("final_test", "wind", "cov2_largest_quarter")())),
    ("5.1 Calibration", "Scales per channel: solar / wind", "1.51 / 1.25", lambda: (kappa_ch("solar")(), kappa_ch("wind")())),
    ("5.1 Calibration", "Final test, solar ±1σ with the channel scale (%)", "85",
     calcond("final_test", "solar", "all", "channel", "cov1")),
    ("5.1 Calibration", "Final test, wind ±2σ on the largest-error quarter with the channel scale (%)", "81",
     calcond("final_test", "wind", "largest quarter", "channel", "cov2")),
    ("5.1 Calibration", "Channel scales in closed loop: cost (%) / protection (h), at most", "0.1 / 0.25", chan_effect),
    ("5.3 Decision map", "C: largest cost difference (%)", "0.03", m("mapCCostMax")),
    ("5.3 Decision map", "C: cells where the configuration-blind ratio predicts a difference", "4", m("mapCOldPred")),
    ("5.3 Decision map", "Surplus ratio: largest tie / smallest difference", "0.041 / 0.082",
     lambda: (macros()["mapBUpLowThr"], macros()["mapBUpHighThr"])),
    ("5.3 Decision map", "Bounds with Q0.90", "0.015 / 0.031", rq_bounds("q90")),
    ("5.3 Decision map", "Bounds with Q0.99", "0.048 / 0.096", rq_bounds("q99")),
    ("5.3 Decision map", "A: largest r_E", "0.094", map_A_rmax),
    ("5.3 Decision map", "A: UA-MPC protection at most (h)", "1.0", m("mapAUaMax")),
    ("5.3 Decision map", "A: DET-BO protection (h)", "0.75–12.00", lambda: (macros()["mapABoMin"], macros()["mapABoMax"])),
    ("5.3 Decision map", "A: UA-MPC cheaper from 72 Wh up (%)", "1.8–6.0", lambda: (macros()["mapACostMin"], macros()["mapACostMax"])),
    ("5.3 Decision map", "A: DET-BO protection at the upper edge (%)", "83", m("mapABoUpShare")),
    ("5.3 Decision map", "B: DET-BO protection at the upper edge in difference cells (%)", "81", m("mapBHighUpShare")),
    ("5.4 Pre-registered", "Γ of the five missed predictions", "0.99–1.64", lambda: (misses().Gamma.min(), misses().Gamma.max())),
    ("5.4 Pre-registered", "Missed new cell (Γ = 0.99): DET-BO / UA-MPC (h)", "8.00 / 3.75",
     lambda: tuple(misses()[misses().set.str.startswith("New")][["bo_h", "ua_h"]].iloc[0])),
    ("5.4 Pre-registered", "Missed correlation cells: UA-MPC fewer hours", "5.25 / 6.50",
     lambda: tuple(sorted(misses()[misses().set.str.startswith("Error")].diff_h))),
    ("5.4 Pre-registered", "Correlation, 108 Wh: r_E at φ = 0.5 / 0.857", "0.029 / 0.050", lambda: (corr_cell(0.5, "r_E")(), corr_cell(0.857, "r_E")())),
    ("5.4 Pre-registered", "Correlation, φ = 0.5: DET-BO / UA-MPC (h)", "0.75 / 0.00", lambda: (corr_cell(0.5, "bo")(), corr_cell(0.5, "ua")())),
    ("5.4 Pre-registered", "Correlation, φ = 0.857: DET-BO / UA-MPC (h)", "6.25 / 1.00", lambda: (corr_cell(0.857, "bo")(), corr_cell(0.857, "ua")())),
    ("5.4 Pre-registered", "Correlation, φ = 0.98: DET-BO / UA-MPC (h)", "17.75 / 11.25", lambda: (corr_cell(0.98, "bo")(), corr_cell(0.98, "ua")())),
    ("5.4 Pre-registered", "24-h accumulated error at φ = 0.5 / 0.857", "0.15 / 0.27", lambda: (corr_cell(0.5, "S24")(), corr_cell(0.857, "S24")())),
    ("5.4 Pre-registered", "Sweep, Γ = 0.5: fewer protection hours than UA-MPC", "0.5–1.0", lambda: (min(sweep_stats(0.5)()[0]), max(sweep_stats(0.5)()[0]))),
    ("5.4 Pre-registered", "Sweep, Γ = 0.5: extra cost (%)", "0.5–1.6", lambda: (min(sweep_stats(0.5)()[1]), max(sweep_stats(0.5)()[1]))),
    ("5.4 Pre-registered", "Sweep, Γ = 0.75: cost difference (%)", "-0.6–0.1", lambda: (min(sweep_stats(0.75)()[1]), max(sweep_stats(0.75)()[1]))),
    ("5.4 Pre-registered", "r_E of the base cell: tuning / development / final test", "0.026 / 0.023 / 0.035",
     lambda: (eg("tuning", "r_E")(), eg("development", "r_E")(), eg("final_test", "r_E")())),
    ("5.4 Pre-registered", "r_E in winter: mean / times the tuning value", "0.064 / 2.4",
     lambda: (csv("ratio/effective_gamma.csv").query("period.str.startswith('winter')").r_E.mean(),
              csv("ratio/effective_gamma.csv").query("period.str.startswith('winter')").r_E.mean() / eg("tuning", "r_E")())),
    ("5.4 Pre-registered", "T1 bootstrap, UA-MPC minus re-sized back-off (m€) and 95 % interval", "-32 / -45 / -21",
     lambda: tuple(1000 * t1_boot()[0][k] for k in ("diff_total", "lo_total", "hi_total"))),
    ("5.4 Pre-registered", "T1 bootstrap, UA-MPC minus summer-tuned DET-BO (%) and interval (m€)", "-0.08 / -10 / 2",
     lambda: (t1_boot()[1]["diff_pct"], 1000 * t1_boot()[1]["lo_total"], 1000 * t1_boot()[1]["hi_total"])),
    ("5.4 Pre-registered", "T1, summer final test: re-sized back-off / UA-MPC (h)", "10.00 / 9.50",
     lambda: (jload(EXT / "preregistered/t1/steps/det_bo_sized_final.json")["metrics"]["protection_h"], final_metric("B", "ua", "protection_h")())),
    ("5.4 Pre-registered", "T1, summer final test: extra cost (%)", "0.07",
     lambda: 100 * (jload(EXT / "preregistered/t1/steps/det_bo_sized_final.json")["metrics"]["net_cost_eur"] / final_metric("B", "ua", "net_cost_eur")() - 1)),
    ("5.4 Pre-registered", "T1: largest monthly extra cost vs UA-MPC (%)", "1.7", t1_monthly_extra_max),
    ("5.4 Pre-registered", "Month-to-month change of r_E in winter (factor)", "0.79–1.37", gcheck(("rE_ratio_min", "rE_ratio_max"))),
    ("5.4 Pre-registered", "Spearman ρ, protection of DET-BO against the realised Γ (11 winter month-runs)", "0.94", gcheck("spearman_rho")),
    ("5.4 Pre-registered", "Realised Γ of the two DET-BO months without protection", "0.6 / 0.6", gcheck("zero_protection_gamma")),
    ("5.4 Pre-registered", "Windows inside the tightened band, deficit within the reserve, with a lower-edge intervention (summer-tuned)",
     "0 of 3022", gcheck("windows", "det_bo", ("inside_within_lower_edge", "inside_within"))),
    ("5.4 Pre-registered", "The same, re-sized back-off", "0 of 3228", gcheck("windows", "det_bo_sized", ("inside_within_lower_edge", "inside_within"))),
    ("5.4 Pre-registered", "Lower-edge interventions in windows that started with the reserve used (summer-tuned)", "60 of 63",
     gcheck("windows", "det_bo", ("reserve_used_lower_edge", "lower_edge_hours"))),
    ("5.4 Pre-registered", "The same, re-sized back-off", "16 of 16", gcheck("windows", "det_bo_sized", ("reserve_used_lower_edge", "lower_edge_hours"))),
    ("5.4 Pre-registered", "Scenario margin: Γ", "0.15–0.62", scen("Gamma_scenario")),
    ("5.4 Pre-registered", "Robust (tube) margin: Γ", "0.09–0.59", scen("Gamma_robust")),
    ("5.4 Pre-registered", "Usable band held back, scenario margin (%)", "14–35", scen("band_locked_pct_scenario")),
    ("5.4 Pre-registered", "Usable band held back, robust margin (%)", "23–53", scen("band_locked_pct_robust")),
    ("5.4 Pre-registered", "Usable band held back, re-sized back-off (%)", "7–18", scen("band_locked_pct_backoff_resized")),
    ("5.4 Pre-registered", "Γ of the re-sized back-off, summer periods and T1 months", "0.52–1.03", scen("Gamma_backoff_resized")),
    ("6 Discussion", "Usable band held back, scenario and robust margins (%)", "14–53", scen("band_locked_pct_scenario", "band_locked_pct_robust")),
    ("5.4 Pre-registered", "T3, Nov–Mar protection: scenario / robust back-off (h)", "2.75 / 3.00", lambda: (t3("scenario")(), t3("robust")())),
    ("5.4 Pre-registered", "T3, Nov–Mar extra net cost vs UA-MPC: scenario / robust (%)", "1.6 / 1.9",
     lambda: tuple(100 * (t3(r, "net_cost_eur")() / winter_ref("ua")[0] - 1) for r in ("scenario", "robust"))),
    ("5.4 Pre-registered", "T3, summer final test protection: scenario / robust (h)", "8.25 / 3.50", lambda: (t3_final("scenario")(), t3_final("robust")())),
    ("5.4 Pre-registered", "T3, summer final test extra cost vs re-sized back-off: scenario / robust (%)", "1.1 / 3.2",
     lambda: tuple(100 * (t3_final(r, "net_cost_eur")() / jload(EXT / "preregistered/t1/steps/det_bo_sized_final.json")["metrics"]["net_cost_eur"] - 1)
                   for r in ("scenario", "robust"))),
    ("6 Discussion", "T3, extra winter cost vs UA-MPC (%)", "1.6–1.9",
     lambda: tuple(100 * (t3(r, "net_cost_eur")() / winter_ref("ua")[0] - 1) for r in ("scenario", "robust"))),
    ("5.5 Winter", "Winter coverage ±1σ / ±2σ (%)", "71–86", lambda: (macros()["winCovOneMin"], macros()["winCovOneMax"])),
    ("5.5 Winter", "Winter coverage ±2σ (%)", "86–98", lambda: (macros()["winCovTwoMin"], macros()["winCovTwoMax"])),
    ("5.5 Winter", "Winter RMSE (W per step)", "1.46–2.54", lambda: (macros()["winRmseMin"], macros()["winRmseMax"])),
    ("5.5 Winter", "PIT lowest decile, five winter months (%)", "10–14",
     lambda: tuple(sorted(csv("calibration/calib_diag.csv").query("period.str.startswith('winter')").pit_lowest_decile)[-5:][i] for i in (0, -1))),
    ("5.5 Winter", "PIT lowest decile, summer periods (%)", "1–5",
     lambda: (csv("calibration/calib_diag.csv").query("~period.str.startswith('winter')").pit_lowest_decile.min(),
              csv("calibration/calib_diag.csv").query("~period.str.startswith('winter')").pit_lowest_decile.max())),
    ("5.5 Winter", "Γ in December: UA-MPC / DET-BO", "1.84 / 1.77", lambda: (eg("winter m3", "Gamma_UA")(), eg("winter m3", "Gamma_BO")())),
    ("5.5 Winter", "Γ in March: UA-MPC / DET-BO", "1.77 / 2.31", lambda: (eg("winter m6", "Gamma_UA")(), eg("winter m6", "Gamma_BO")())),
    ("5.5 Winter", "Seasonal re-selection: threshold for November (h)", "6.8", lambda: jload(EXT / "seasonal/reselection.json")["threshold_h"]),
    ("5.5 Winter", "Re-selected UA-MPC point in November (€ / h)", "0.908 / 3.75", lambda: (reselect("ua", "nov_cost")(), reselect("ua", "nov_prot_h")())),
]

# values that need the trained forecast models (level 2: Experiments, "Forecast calibration")
MODEL_CLAIMS = [
    ("5.1 Calibration", "Calibrated forecast, development: ±1σ / ±2σ coverage (%)", "80.1 / 96.6", "constant_all_cov1 / constant_all_cov2"),
    ("5.1 Calibration", "Quarter of days with the largest errors: ±1σ / ±2σ (%)", "64.6 / 91.7", "constant_hard_days_cov1 / constant_hard_days_cov2"),
    ("4.2 Calibration", "Calibration period: RMSE / RMS of the GP std (W)", "1.67 / 1.24", "rmse_w / rms_sigma_w"),
    ("4.2 Calibration", "Raw GP coverage ±1σ / ±2σ (%)", "66.5 / 88.9", "coverage_1sigma_pct / coverage_2sigma_pct"),
    ("4.2 Calibration", "Scale κ", "1.345", "sigma_scale"),
    ("4.2 Calibration", "Lag-1 autocorrelation of the hourly errors / φ per step", "0.54 / 0.857", "error_autocorr_1h / error_autocorr_step"),
]

# numbers quoted from other studies (Section 5.7) are not results of this package and are not checked


# ---------------------------------------------------------------- comparison
def _parse(printed: str):
    """'0.783 / 0.809' -> [('0.783', 3), ...]; '7.2–38.2' -> range; '28 of 33' -> count."""
    if " of " in printed:
        a, b = printed.split(" of "); return "count", [(float(a), 0), (float(b), 0)]
    parts = re.split(r"\s*/\s*|(?<=\d)\s*–\s*", printed)
    vals = [(float(p), len(p.split(".")[1]) if "." in p else 0) for p in parts]
    return ("range" if "–" in printed else "list"), vals


def check(printed: str, computed) -> tuple[bool, str]:
    kind, ref = _parse(printed)
    got = list(computed) if isinstance(computed, (tuple, list)) else [computed]
    if len(got) != len(ref):
        return False, f"{len(got)} values for {len(ref)}"
    shown = []
    ok = True
    for (r, dec), g in zip(ref, got):
        g = abs(float(g)) if r > 0 and float(g) < 0 and kind != "count" else float(g)
        rounded = float(f"{g:.{dec}f}")  # same formatting as the scripts that printed the paper's numbers
        shown.append(f"{rounded:.{dec}f}")
        ok &= abs(rounded - r) < 0.5 * 10 ** (-dec) + 1e-12
    sep = " of " if kind == "count" else ("–" if kind == "range" else " / ")
    return ok, sep.join(shown)


def source_of(fn, statement: str) -> str:
    """Where a value comes from: the result file(s) or the macro of experiments/analysis/make_numbers.py."""
    names = list(getattr(fn, "macros", []))
    if not names:
        try:
            names = re.findall(r'macros\(\)\["(\w+)"\]', inspect.getsource(fn))
        except (OSError, TypeError):
            names = []
    if names:
        return "experiments/analysis/make_numbers.py (results/summer, results/winter, results/analysis): " + ", ".join("\\" + n for n in names)
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return ""
    helpers = {n for n in re.findall(r"\b([a-z_0-9]+)\(", src) if n in globals() and callable(globals()[n])}
    files = set(re.findall(r'"((?:[\w./{}-]+/)?[\w{}-]+\.(?:csv|json))"', src))
    for h in helpers:
        try:
            files |= set(re.findall(r'"((?:[\w./{}-]+/)?[\w{}-]+\.(?:csv|json))"', inspect.getsource(globals()[h])))
        except (OSError, TypeError):
            pass
    return "app/catalogue.py; " + ", ".join(sorted(files)) if files else "app/catalogue.py"


def model_values() -> dict | None:
    p = OUT / "rerun" / "calibration.json"
    return json.loads(p.read_text()) if p.exists() else None


def evaluate() -> list[dict]:
    macros.cache_clear()
    rows = []
    for sec, what, printed, fn in CLAIMS:
        try:
            ok, got = check(printed, fn())
            err = ""
        except Exception as e:  # missing file or column: reported, not hidden
            ok, got, err = False, "–", f"{type(e).__name__}: {e}"
        rows.append({"section": sec, "statement": what, "paper": printed, "reproduced": got, "ok": ok, "error": err,
                     "source": source_of(fn, what), "status": "ok" if ok else "differs"})
    mv = model_values()
    for sec, what, printed, keys in MODEL_CLAIMS:
        src = "code/src/calibrate_uncertainty.py with the summer GP models (Experiments: Forecast calibration)"
        if mv is None:
            rows.append({"section": sec, "statement": what, "paper": printed, "reproduced": "needs models", "ok": None,
                         "error": "", "source": src, "status": "needs models"})
            continue
        try:
            ok, got = check(printed, tuple(mv[k.strip()] for k in keys.split("/")))
            err = ""
        except Exception as e:
            ok, got, err = False, "–", f"{type(e).__name__}: {e}"
        rows.append({"section": sec, "statement": what, "paper": printed, "reproduced": got, "ok": ok, "error": err, "source": src,
                     "status": "ok" if ok else "differs"})
    order = {s: i for i, s in enumerate(["3.1 Cell", "3.2 Data", "3.2 Periods", "4.2 Calibration", "5.1 Calibration", "5.2 Final test",
                                        "5.3 Decision map", "5.4 Pre-registered", "5.5 Winter", "6 Discussion", "S2 Sensitivity", "S2 Tariffs"])}
    rows.sort(key=lambda r: order.get(r["section"], 99))
    return rows
