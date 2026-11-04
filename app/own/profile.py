"""Own load data: read, check, and map onto the simulated months; convert sizes between the site and the cell.

Input: a CSV with a timestamp column and one load column, 15-, 30- or 60-minute resolution, as average power
(kW or W) or energy per interval (kWh). The series is put on the 15-minute grid of the replay (an hourly value
holds for its four quarters), short gaps are interpolated, and every simulated step is then taken either
directly (where the data cover that date) or from the profile: the sample with the same weekday and time of day
from the nearest day of the year in the data ("profile mode"). Public holidays are not treated separately.

The replay runs on the reference laboratory-scale cell (mean load 10.788 W). The site is mapped onto it by one
factor f = 10.788 W / mean site load; battery, power limit, PV and wind are scaled with the same factor, and
costs and energies are scaled back by 1/f, so the results are in the site's units.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

TZ = "Europe/Budapest"
CELL_MEAN_W = 10.788           # mean load of the reference cell (W)
CELL = {"battery_wh": 72.0, "power_w": 24.0, "pv_w": 30.0,
        # rated power of the cell's wind model: 0.5 rho A cp v_rated^3 (code/config/dataConfig.json)
        "wind_w": 0.5 * 1.225 * 0.32 * 0.2 * 8.0 ** 3}
MAX_GAP_FILL_H = 2.0           # gaps up to this length are interpolated; longer ones come from the profile
MIN_DAYS = 7


def reference_ratios() -> dict:
    """The reference system's sizes as multiples of its mean load (battery in hours of mean load)."""
    return {"battery_h": CELL["battery_wh"] / CELL_MEAN_W, "power_x": CELL["power_w"] / CELL_MEAN_W,
            "pv_x": CELL["pv_w"] / CELL_MEAN_W, "wind_x": CELL["wind_w"] / CELL_MEAN_W}


def _num(s: pd.Series) -> pd.Series:
    if not pd.api.types.is_numeric_dtype(s):
        s = s.astype(str).str.strip().str.replace(" ", "").str.replace(" ", "")
        # decimal comma (1,25) when no dot is used
        if s.str.contains(",").any() and not s.str.contains(r"\.").any():
            s = s.str.replace(",", ".")
    return pd.to_numeric(s, errors="coerce")


def read(text: str, unit: str = "kw", tz: str = TZ) -> tuple[pd.Series | None, dict]:
    """Parse and check the upload. Returns (load in kW on a 15-min UTC grid, NaN where unknown; report)."""
    rep = {"errors": [], "warnings": [], "unit": unit, "timezone": tz}
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", comment="#")
    except Exception as e:
        rep["errors"].append(f"the file could not be read as CSV ({e})")
        return None, rep
    if df.shape[1] < 2:
        rep["errors"].append("two columns are needed: timestamp and load")
        return None, rep
    # a file without header: the first row parses as data
    first = list(df.columns)
    if pd.notna(pd.to_datetime(first[0], errors="coerce")) and pd.notna(_num(pd.Series([first[1]])).iloc[0]):
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", comment="#", header=None)
    tcol, vcol = df.columns[0], df.columns[1]
    rep["columns"] = [str(tcol), str(vcol)]
    try:
        t = pd.to_datetime(df[tcol], errors="coerce", utc=False, format="mixed")
    except ValueError:   # different UTC offsets in one column (summer and winter time): parse them as UTC
        t = pd.to_datetime(df[tcol], errors="coerce", utc=True, format="mixed")
    v = _num(df[vcol])
    bad_t, bad_v = int(t.isna().sum()), int(v.isna().sum())
    if bad_t:
        rep["warnings"].append(f"{bad_t} rows with an unreadable timestamp were skipped")
    if bad_v:
        rep["warnings"].append(f"{bad_v} rows with an unreadable load value were skipped")
    ok = t.notna() & v.notna()
    t, v = t[ok], v[ok].astype(float)
    if len(t) < 2:
        rep["errors"].append("fewer than two valid rows")
        return None, rep
    # time zone: offsets in the file win; otherwise the chosen zone (DST: ambiguous hours inferred)
    if getattr(t.dt, "tz", None) is not None:
        t = t.dt.tz_convert("UTC")
        rep["timezone"] = "from the file (UTC offsets)"
    elif tz.upper() == "UTC":
        t = t.dt.tz_localize("UTC")
    else:
        try:
            t = t.dt.tz_localize(tz, ambiguous="infer", nonexistent="shift_forward")
        except Exception:
            t = t.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
            rep["warnings"].append("ambiguous DST hours could not be resolved and were dropped")
        t = t.dt.tz_convert("UTC")
    s = pd.Series(v.to_numpy(), index=pd.DatetimeIndex(t)).loc[lambda x: x.index.notna()].sort_index()
    dup = int(s.index.duplicated().sum())
    if dup:
        rep["warnings"].append(f"{dup} duplicate timestamps were averaged")
        s = s.groupby(level=0).mean()
    step = pd.Series(s.index).diff().dt.total_seconds().div(60).median()
    if not np.isfinite(step) or round(step) not in (15, 30, 60):
        rep["errors"].append(f"the resolution must be 15, 30 or 60 minutes (found {step:g} min)")
        return None, rep
    step = int(round(step)); rep["interval_min"] = step
    if unit == "kwh":
        s = s / (step / 60.0)           # energy per interval -> average power
    elif unit == "w":
        s = s / 1000.0
    neg = int((s < 0).sum())
    if neg:
        msg = f"{neg} negative values (export or a sign convention?) were treated as missing"
        (rep["errors"] if neg > 0.01 * len(s) else rep["warnings"]).append(msg)
        s[s < 0] = np.nan
    # 15-minute grid; a value of a 30/60-min interval holds for each of its quarters
    full = pd.date_range(s.index[0], s.index[-1] + pd.Timedelta(minutes=step - 15), freq="15min")
    on = s.reindex(s.index.union(full)).ffill(limit=step // 15 - 1).reindex(full) if step > 15 else s.reindex(full)
    # reported gaps: runs of missing quarters on the grid
    miss = on.isna().to_numpy()
    runs = []
    if miss.any():
        d = np.diff(np.r_[0, miss.astype(int), 0])
        runs = list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))
    gaps = [{"from": str(full[i]), "hours": (j - i) * 0.25} for i, j in runs]
    filled = on.interpolate(method="time", limit_area="inside")
    for i, j in runs:           # long gaps stay open (filled from the profile later)
        if (j - i) * 0.25 > MAX_GAP_FILL_H:
            filled.iloc[i:j] = np.nan
    rep.update(rows=int(len(s)), start=str(full[0].tz_convert(TZ)), end=str(full[-1].tz_convert(TZ)), days=round(len(full) / 96, 1),
               gaps=len(gaps), gap_hours=float(sum(g["hours"] for g in gaps)),
               longest_gap_h=float(max((g["hours"] for g in gaps), default=0.0)),
               interpolated_steps=int(filled.notna().sum() - on.notna().sum()), missing_steps=int(filled.isna().sum()),
               gap_list=gaps[:20])
    x = filled.dropna()
    if len(x) == 0:
        rep["errors"].append("no valid load values")
        return None, rep
    rep.update(mean_kw=float(x.mean()), max_kw=float(x.max()), min_kw=float(x.min()), p95_kw=float(x.quantile(0.95)),
               energy_mwh_per_year=float(x.mean() * 8.76))
    zeros = float((x == 0).mean() * 100)
    rep["zeros_pct"] = zeros
    if zeros > 5:
        rep["warnings"].append(f"{zeros:.1f} % of the steps are zero (shutdowns or missing data written as 0?)")
    if x.mean() <= 0:
        rep["errors"].append("the mean load is zero")
    med = float(x.median())
    if med > 0 and (x > 20 * med).any():
        rep["warnings"].append(f"{int((x > 20 * med).sum())} steps above 20 times the median load (spikes or unit mix-up?)")
    loc = x.index.tz_convert(TZ)
    slots = pd.Index(loc.dayofweek * 96 + loc.hour * 4 + loc.minute // 15).nunique()
    rep["weekly_slots_covered"] = int(slots)
    if (full[-1] + pd.Timedelta(minutes=15) - full[0]) < pd.Timedelta(days=MIN_DAYS) or slots < 672:
        rep["errors"].append(f"at least one full week is needed so that every weekday and quarter-hour is known "
                             f"({slots} of 672 covered)")
    return filled, rep


def map_to(index_utc: pd.DatetimeIndex, load_kw: pd.Series) -> tuple[np.ndarray, float]:
    """Load (kW) at the simulated steps: the data where they cover the step, otherwise the profile (same weekday
    and quarter-hour, nearest day of the year in the data). Returns (values, share taken directly)."""
    x = load_kw.dropna()
    direct = x.reindex(index_utc).to_numpy()
    out = direct.copy()
    need = np.isnan(out)
    if need.any():
        lx = x.index.tz_convert(TZ)
        key_x = lx.dayofweek * 96 + lx.hour * 4 + lx.minute // 15
        doy_x = lx.dayofyear.to_numpy()
        lt = index_utc[need].tz_convert(TZ)
        key_t = lt.dayofweek * 96 + lt.hour * 4 + lt.minute // 15
        doy_t = lt.dayofyear.to_numpy()
        vals = x.to_numpy()
        res = np.full(len(lt), np.nan)
        order = np.argsort(key_x, kind="stable")
        ks, starts = np.unique(np.asarray(key_x)[order], return_index=True)
        ends = np.r_[starts[1:], len(order)]
        groups = {int(k): order[a:b] for k, a, b in zip(ks, starts, ends)}
        for k in np.unique(key_t):
            sel = np.where(np.asarray(key_t) == k)[0]
            g = groups.get(int(k))
            if g is None:
                continue
            dd = np.abs(doy_t[sel][:, None] - doy_x[g][None, :])
            dd = np.minimum(dd, 366 - dd)          # circular distance over the year
            res[sel] = vals[g][np.argmin(dd, axis=1)]
        out[need] = res
    return out, float(np.mean(~need)) if len(need) else 1.0


def cell_factor(mean_kw: float) -> float:
    """f such that site W x f = cell W."""
    return CELL_MEAN_W / (mean_kw * 1000.0)


def sizes_to_cell(sizes: dict, mean_kw: float) -> dict:
    """Site sizes (battery kWh, power kW, PV kWp, wind kW rated) -> cell values and renewable scale factors."""
    f = cell_factor(mean_kw)
    r = lambda x: float(f"{x:.12g}")  # noqa: E731  (12 significant digits: no float noise from unit conversions)
    return {"E_capacity": r(sizes["battery_kwh"] * 1000 * f), "p_max_w": r(sizes["power_kw"] * 1000 * f),
            "pv_scale": r(sizes["pv_kwp"] * 1000 * f / CELL["pv_w"]), "wind_scale": r(sizes["wind_kw"] * 1000 * f / CELL["wind_w"]),
            "factor": f}


def default_sizes(mean_kw: float) -> dict:
    r = reference_ratios()
    return {"battery_kwh": round(r["battery_h"] * mean_kw, 1), "power_kw": round(r["power_x"] * mean_kw, 1),
            "pv_kwp": round(r["pv_x"] * mean_kw, 1), "wind_kw": round(r["wind_x"] * mean_kw, 1)}
