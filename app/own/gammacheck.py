"""Window-by-window check of the Γ reading for a constant back-off (DET-BO).

A controller with a back-off b re-plans every hour from the measured SoC and keeps its plan b inside the SoC band.
If a re-plan starts inside the tightened band (SoC >= x_min + b), the SoC can reach the lower edge before the next
re-plan only if the forecast deficit routed to the battery, accumulated within that hour, exceeds the energy the
back-off holds (b E, and b E eta on the discharge side). This module classifies every re-planning hour of a run:

    started inside the tightened band, deficit within the reserve   -> the bound says: no lower-edge intervention
    started inside the tightened band, deficit above the reserve    -> an intervention is possible
    started with the reserve already used by the preceding hours    -> the bound does not apply

and counts the hours with a lower-edge intervention (BMS stopped the discharge) in each class. The same code serves
the study (experiments/extended/ratio/gamma_windows.py) and the per-run report of the simulator.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CLASSES = ("inside_within", "inside_above", "reserve_used")


def routed(mu: pd.Series, real: pd.Series, arch: str, p_ch: float, p_dis: float) -> pd.Series:
    """Forecast error that reaches the battery (W, positive = less renewable power than forecast), as in r_E."""
    e = mu - real
    if arch == "renewable_buffer":
        return np.minimum(e.clip(lower=0), np.minimum(mu.clip(lower=0), p_ch))
    if arch == "self_consumption":
        return e.clip(-(p_ch + p_dis), p_ch + p_dis)
    return e * 0.0


def windows(steps: pd.DataFrame, err_w: pd.Series, b: float, e_cap: float, soc0: float | None,
            soc_min: float = 0.1, eta: float = 0.95, replan: int = 4, dt_h: float = 0.25) -> pd.DataFrame:
    """One row per re-planning window of a run.

    steps: the closed-loop steps (t, soc after the step, cut_discharge_w, cut_charge_w), starting at a re-plan.
    err_w: routed forecast error per step (W), indexed by t. soc0: SoC before the first step (None: first window
    is left unclassified)."""
    t = pd.to_datetime(steps["t"], utc=True)
    e = err_w.reindex(t).to_numpy(dtype=float) * dt_h                     # Wh per step
    soc = steps["soc"].to_numpy(dtype=float)
    lo = (steps["cut_discharge_w"].to_numpy(dtype=float) > 1e-3)
    up = (steps["cut_charge_w"].to_numpy(dtype=float) > 1e-3) & ~lo
    reserve = b * e_cap * eta                                              # Wh the back-off holds above x_min
    rows = []
    for w in range(0, len(steps) // replan * replan, replan):
        s0 = soc0 if w == 0 else soc[w - 1]
        cum = np.cumsum(np.nan_to_num(e[w:w + replan]))
        deficit = max(0.0, float(cum.max()))                               # worst running deficit in the hour
        if s0 is None or s0 != s0:
            cls = None
        elif s0 >= soc_min + b - 1e-9:
            cls = "inside_within" if deficit <= reserve else "inside_above"
        else:
            cls = "reserve_used"
        rows.append({"t": t.iloc[w], "soc0": s0, "deficit_wh": deficit, "reserve_wh": reserve,
                     "gamma_window": deficit / reserve if reserve > 0 else np.nan, "class": cls,
                     "lower_edge": bool(lo[w:w + replan].any()), "upper_edge": bool(up[w:w + replan].any())})
    return pd.DataFrame(rows)


def summary(win: pd.DataFrame) -> dict:
    """Hours (windows) and lower-edge intervention hours per class."""
    out = {"hours": int(len(win)), "lower_edge_hours": int(win["lower_edge"].sum()) if len(win) else 0,
           "upper_edge_hours": int(win["upper_edge"].sum()) if len(win) else 0}
    for c in CLASSES:
        sel = win["class"] == c if len(win) else pd.Series(dtype=bool)
        out[c] = int(sel.sum())
        out[c + "_lower_edge"] = int((sel & win["lower_edge"]).sum()) if len(win) else 0
    return out
