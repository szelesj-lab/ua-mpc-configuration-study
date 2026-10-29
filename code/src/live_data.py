"""Storage for live measurements from the real system.

Measurements arrive through the dashboard API (POST /api/live, see README) and are kept
in one CSV file per day under data/live/ (UTC dates). Every field except the timestamp
is optional, so a system can start by sending only what it already measures.
"""

import csv
import math
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from settings import DATA_DIR, load_config

LIVE_DIR = DATA_DIR / "live"

# name -> (description, unit)
MEASUREMENT_FIELDS = {
    "pv_w": ("PV power", "W"),
    "wind_w": ("Wind power", "W"),
    "load_w": ("Load / demand", "W"),
    "grid_w": ("Grid import", "W"),
    "battery_w": ("Battery power (+ discharge, − charge)", "W"),
    "soc": ("Battery state of charge", "0–1"),
    "curtailed_w": ("Curtailed renewable power", "W"),
}
# Power balance of the cell (renewables feed the DC bus with the battery, the grid feeds the load):
#   load_w = grid_w + battery_w + pv_w + wind_w - curtailed_w
COLUMNS = ["timestamp", *MEASUREMENT_FIELDS]

_lock = threading.Lock()


def live_settings(config=None) -> dict:
    cfg = (config or load_config()).get("live", {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "api_token": str(cfg.get("api_token", "") or ""),
        "stale_after_s": float(cfg.get("stale_after_s", 120)),
        "use_measured_soc": bool(cfg.get("use_measured_soc", True)),
        "measured_soc_max_age_s": float(cfg.get("measured_soc_max_age_s", 900)),
        "auto_mpc": bool(cfg.get("auto_mpc", True)),
        "mpc_interval_min": float(cfg.get("mpc_interval_min", 15)),
        "deviation_abs_w": float(cfg.get("deviation_abs_w", 2.0)),
        "deviation_rel": float(cfg.get("deviation_rel", 0.25)),
        "deviation_soc": float(cfg.get("deviation_soc", 0.05)),
    }


def _site_tz(config=None):
    return (config or load_config()).get("general", {}).get("timezone", "Europe/Budapest")


def parse_measurement(record: dict, now: datetime | None = None, config=None) -> dict:
    """Validate one measurement and return it with a UTC timestamp.

    timestamp: ISO 8601. Without a UTC offset it is read as site local time
    (general.timezone). If missing, the time of arrival is used.
    """
    if not isinstance(record, dict):
        raise ValueError("each measurement must be a JSON object")
    unknown = set(record) - set(COLUMNS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}; allowed: {', '.join(COLUMNS)}")

    ts_raw = record.get("timestamp")
    if ts_raw in (None, ""):
        ts = pd.Timestamp(now or datetime.now(timezone.utc))
    else:
        try:
            ts = pd.Timestamp(ts_raw)
        except (ValueError, TypeError):
            raise ValueError(f"timestamp '{ts_raw}' is not ISO 8601 (e.g. 2026-10-16T14:30:00+02:00)") from None
    if ts.tzinfo is None:
        ts = ts.tz_localize(_site_tz(config))
    ts = ts.tz_convert("UTC")

    out = {"timestamp": ts}
    for name in MEASUREMENT_FIELDS:
        if name not in record or record[name] is None:
            continue
        val = record[name]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError(f"{name} must be a number")
        if name == "soc" and not 0.0 <= val <= 1.0:
            raise ValueError("soc must be between 0 and 1 (e.g. 0.55 for 55 %)")
        out[name] = float(val)
    if len(out) == 1:
        raise ValueError(f"the measurement has no values; send at least one of {', '.join(MEASUREMENT_FIELDS)}")
    return out


def _upgrade_file(path):
    """Rewrite a day file written with fewer columns (older version) to the current columns."""
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    if header != COLUMNS:
        df = pd.read_csv(path)
        df.reindex(columns=COLUMNS).to_csv(path, index=False)


def balance_residual(values: dict):
    """load - (grid + battery + pv + wind - curtailed) in W, or None if a term is missing.

    pv/wind/curtailed count as 0 when absent, the other three terms are required.
    """
    if any(values.get(k) is None for k in ("load_w", "grid_w", "battery_w")):
        return None
    supply = values["grid_w"] + values["battery_w"] + (values.get("pv_w") or 0.0) + (values.get("wind_w") or 0.0) \
        - (values.get("curtailed_w") or 0.0)
    return values["load_w"] - supply


def append_measurements(records, config=None) -> int:
    rows = [parse_measurement(r, config=config) for r in records]
    with _lock:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        checked = set()
        for row in rows:
            path = LIVE_DIR / f"{row['timestamp']:%Y-%m-%d}.csv"
            new_file = not path.exists()
            if not new_file and path not in checked:
                _upgrade_file(path)
                checked.add(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(COLUMNS)
                w.writerow([row["timestamp"].isoformat()] + [row.get(c, "") for c in MEASUREMENT_FIELDS])
    return len(rows)


def read_history(hours=24.0, now: datetime | None = None) -> pd.DataFrame:
    """Measurements of the last `hours`, sorted by time (UTC timestamps)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC")
    start = now - timedelta(hours=hours)
    frames = []
    day = start.normalize()
    while day <= now.normalize():
        path = LIVE_DIR / f"{day:%Y-%m-%d}.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
        day += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    # files written by older versions lack newer fields: always return every column
    df = pd.concat(frames, ignore_index=True).reindex(columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    df = df[(df["timestamp"] >= start) & (df["timestamp"] <= now)]
    return df.sort_values("timestamp").reset_index(drop=True)


def latest(now: datetime | None = None, lookback_hours=48.0) -> dict:
    """Most recent value of every field (fields can arrive in different messages)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC")
    df = read_history(lookback_hours, now)
    out = {"timestamp": None, "age_s": None, "values": {}}
    if df.empty:
        return out
    out["timestamp"] = df["timestamp"].iloc[-1].isoformat()
    out["age_s"] = (now - df["timestamp"].iloc[-1]).total_seconds()
    for name in MEASUREMENT_FIELDS:
        col = df[["timestamp", name]].dropna()
        if not col.empty:
            out["values"][name] = {
                "value": float(col[name].iloc[-1]),
                "timestamp": col["timestamp"].iloc[-1].isoformat(),
                "age_s": (now - col["timestamp"].iloc[-1]).total_seconds(),
            }
    return out


def measured_soc(config=None, now: datetime | None = None):
    """Latest measured SoC if live data is enabled, wanted and recent enough, else None."""
    s = live_settings(config)
    if not (s["enabled"] and s["use_measured_soc"]):
        return None
    soc = latest(now).get("values", {}).get("soc")
    if soc is None or soc["age_s"] > s["measured_soc_max_age_s"]:
        return None
    return soc["value"]
