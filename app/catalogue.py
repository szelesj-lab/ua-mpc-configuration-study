"""What the paper reports, where, and how each value is recomputed from the result files.

Every entry holds the value exactly as printed in the paper. A value counts as reproduced when the recomputed
number, rounded to the decimals printed in the paper, equals it. Ranges ("a–b") are checked at both ends,
counts ("28 of 33") exactly.
"""
from __future__ import annotations

import glob
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
    ("Fig. S1", "Second load case: decision map", "fig12_case2_map"),
    ("Fig. S2", "Second load case: variability experiment", "fig13_case2_variability"),
    ("Fig. S3", "Second load case: capacity", "fig14_case2_capacity"),
    ("GA", "Graphical abstract", "graphical_abstract"),
]

# ---------------------------------------------------------------- tables written by the base step (out/manuscript/tables)
TABLES = [
    ("Table 1", "Data inventory", "i_inventory.tex"),
    ("Table 2", "Periods", "i_periods.tex"),
    ("Table 4", "Selected operating points", "i_weights.tex"),
    ("Table 6", "Final test, BMS plant", "i_final.tex"),
    ("Table 7", "Paired bootstrap, final test", "i_boot.tex"),
    ("Table 12", "Walk-forward winter test", "i_winter.tex"),
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
    return lambda: macros()[name]


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


# ---------------------------------------------------------------- comparison
def _parse(printed: str):
    """'0.783 / 0.809' -> [('0.783', 3), ...]; '7.2–38.2' -> range; '28 of 33' -> count."""
    if " of " in printed:
        a, b = printed.split(" of "); return "count", [(float(a), 0), (float(b), 0)]
    parts = re.split(r"\s*/\s*|\s*–\s*", printed)
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
        g = abs(float(g)) if r >= 0 and float(g) < 0 and kind != "count" else float(g)
        rounded = float(f"{g:.{dec}f}")  # same formatting as the scripts that printed the paper's numbers
        shown.append(f"{rounded:.{dec}f}")
        ok &= abs(rounded - r) < 0.5 * 10 ** (-dec) + 1e-12
    sep = " of " if kind == "count" else ("–" if kind == "range" else " / ")
    return ok, sep.join(shown)


def evaluate() -> list[dict]:
    macros.cache_clear()
    rows = []
    for sec, what, printed, fn in CLAIMS:
        try:
            ok, got = check(printed, fn())
            err = ""
        except Exception as e:  # missing file or column: reported, not hidden
            ok, got, err = False, "–", f"{type(e).__name__}: {e}"
        rows.append({"section": sec, "statement": what, "paper": printed, "reproduced": got, "ok": ok, "error": err})
    return rows
