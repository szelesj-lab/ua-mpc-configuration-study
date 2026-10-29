"""Send simulated live measurements to the dashboard, for testing the Live tab before the
real system is connected.

    python src/live_simulator.py                      # one measurement every 5 s
    python src/live_simulator.py --interval 2 --token MYTOKEN
    python src/live_simulator.py --backfill 6         # first send the last 6 hours at once

The simulated cell follows the MPC plan that was in force at each moment (results/plans/ and
results/CostFunction_results.csv, re-read every minute so new MPC runs are picked up):

* renewables = plan forecast + correlated random forecast error (AR(1), std from the plan's sigma)
* load       = planned demand + small noise
* battery    = planned discharge and charge, limited by the available renewable power, the power
               limits and the battery management system (SoC kept inside [soc_min, soc_max])
* grid       = load - discharge; renewable power that is not stored is curtailed

so every measurement satisfies the power balance of the cell:

    load_w = grid_w + battery_w + pv_w + wind_w - curtailed_w      (battery_w: + discharge, - charge)

Without a plan, a simple daily profile in site local time is used. Live data must be enabled in
the configuration (live.enabled). The real system sends the same JSON to POST /api/live (see README).
"""

import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

from settings import RESULTS_DIR, load_config

PLAN_RELOAD_S = 60.0
ERROR_TIME_CONSTANT_S = 1800.0  # forecast errors stay correlated for about half an hour


class PlanBook:
    """All stored plans; answers 'which plan step was in force at time t'."""

    def __init__(self):
        self.plans = []  # (computed UTC, DataFrame indexed by UTC step start)
        self.loaded_at = 0.0

    def reload(self):
        plans = []
        files = sorted((RESULTS_DIR / "plans").glob("plan_*.csv")) if (RESULTS_DIR / "plans").exists() else []
        for path in files:
            try:
                computed = pd.Timestamp(path.stem[5:], tz="UTC")
            except ValueError:
                continue
            plans.append((computed, path))
        latest = RESULTS_DIR / "CostFunction_results.csv"
        if latest.exists():
            plans.append((pd.Timestamp(latest.stat().st_mtime, unit="s", tz="UTC"), latest))
        out = []
        for computed, path in sorted(plans, key=lambda x: x[0]):
            df = pd.read_csv(path)
            if "Date" not in df or df.empty:
                continue
            df.index = pd.to_datetime(df["Date"], utc=True)
            out.append((computed, df.sort_index()))
        self.plans = out
        self.loaded_at = time.monotonic()

    def maybe_reload(self):
        if time.monotonic() - self.loaded_at > PLAN_RELOAD_S:
            self.reload()

    def step(self, ts, step_s):
        """Plan row of the step that contains ts, from the newest plan computed before ts
        (or, for times before any plan was computed, the oldest plan that covers ts)."""
        covering = [(c, df) for c, df in self.plans
                    if df.index[0] <= ts < df.index[-1] + pd.Timedelta(seconds=step_s)]
        if not covering:
            return None
        before = [x for x in covering if x[0] <= ts]
        _, df = before[-1] if before else covering[0]
        pos = df.index.searchsorted(ts, side="right") - 1
        return df.iloc[max(pos, 0)]


def _planned_values(row, ts_local):
    if row is not None:
        solar = float(row.get("Mean_solar_power (W)", 0.0))
        wind = float(row.get("Mean_wind_power (W)", 0.0))
        return {
            "solar": solar, "wind": wind,
            "load": float(row.get("Demand_power (W)", 0.0)),
            "dis": float(row.get("P_bat_dis_opt (W)", 0.0)),
            "ch": float(row.get("P_bat_ch (W)", solar + wind)),
            "sigma": float(row.get("Aggregated_deviation (W)", 1.0)),
        }
    # fallback: simple daily profile in site local time
    hour = ts_local.hour + ts_local.minute / 60
    solar = max(0.0, 25 * math.sin(math.pi * (hour - 6) / 14)) if 6 <= hour <= 20 else 0.0
    wind = 3 + 2 * math.sin(hour / 3)
    load = 24.0 if 7 <= hour < 19 else 0.0
    return {"solar": solar, "wind": wind, "load": load, "dis": min(load, 8.0), "ch": solar + wind, "sigma": 1.5}


class Cell:
    """Simulated production cell with battery, PV and wind."""

    def __init__(self, cfg, rng=None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.soc = cfg["SoC"].get("initial_soc", 0.5)
        self.err = 0.0  # standardised forecast error, AR(1)
        lim = cfg.get("limits", {})
        self.p_ch_max = lim.get("p_charge_max_w", math.inf)
        self.p_dis_max = lim.get("p_discharge_max_w", math.inf)
        self.charge_is_decision = bool(cfg.get("mpc", {}).get("charge_is_decision", False))

    def measure(self, plan_row, ts, dt_s):
        cfg = self.cfg
        tz = cfg.get("general", {}).get("timezone", "Europe/Budapest")
        v = _planned_values(plan_row, ts.tz_convert(tz))

        phi = math.exp(-dt_s / ERROR_TIME_CONSTANT_S)
        self.err = phi * self.err + math.sqrt(1 - phi ** 2) * self.rng.gauss(0, 1)
        sigma = max(v["sigma"], 0.3)
        ren_fc = v["solar"] + v["wind"]
        share = v["solar"] / ren_fc if ren_fc > 0 else 0.5
        pv = max(0.0, v["solar"] + share * sigma * self.err)
        wd = max(0.0, v["wind"] + (1 - share) * sigma * self.err)
        ren = pv + wd
        load = max(0.0, v["load"] + self.rng.gauss(0, 0.5)) if v["load"] > 0 else 0.0

        eta, e_cap = cfg["SoC"]["eta_batt"], cfg["SoC"]["E_capacity"]
        soc_min, soc_max = cfg["SoC"].get("soc_min", 0.0), cfg["SoC"].get("soc_max", 1.0)
        dt_h = dt_s / 3600
        # charging: the planned share of the renewable power (or all of it), within the limits
        ch = min(ren, v["ch"]) if self.charge_is_decision else ren
        ch = max(0.0, min(ch, self.p_ch_max, (soc_max - self.soc) * e_cap / (eta * dt_h)))
        # discharging: the planned power, not more than the load, within the limits
        dis = max(0.0, min(v["dis"], load, self.p_dis_max, (self.soc - soc_min) * e_cap * eta / dt_h))
        self.soc = min(1.0, max(0.0, self.soc + (eta * ch - dis / eta) * dt_h / e_cap))

        r = lambda x: round(x, 3)  # noqa: E731
        # rounded so that the sent values close the power balance exactly
        pv_r, wd_r, dis_r, load_r = r(pv), r(wd), r(dis), r(load)
        curt_r = max(0.0, r(pv_r + wd_r - r(ch)))
        ch_r = r(pv_r + wd_r - curt_r)
        return {
            "timestamp": ts.isoformat(),
            "pv_w": pv_r, "wind_w": wd_r, "load_w": load_r,
            "grid_w": r(load_r - dis_r), "battery_w": r(dis_r - ch_r),
            "curtailed_w": curt_r, "soc": round(self.soc, 4),
        }


def post(url, token, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-API-Key": token} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main():
    parser = argparse.ArgumentParser(description="Send simulated live measurements to the dashboard.")
    parser.add_argument("--url", default="http://127.0.0.1:8050/api/live")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between measurements")
    parser.add_argument("--token", default=None, help="API token (default: live.api_token from the config)")
    parser.add_argument("--backfill", type=float, default=0.0, help="first send this many hours of history")
    args = parser.parse_args()

    cfg = load_config()
    step_s = float(cfg.get("general", {}).get("deltaT", 900))
    token = args.token if args.token is not None else cfg.get("live", {}).get("api_token", "")
    book = PlanBook()
    book.reload()
    cell = Cell(cfg)

    try:
        if args.backfill > 0:
            step = timedelta(minutes=1)
            now = datetime.now(timezone.utc)
            ts = now - timedelta(hours=args.backfill)
            batch = []
            while ts < now:
                t = pd.Timestamp(ts)
                batch.append(cell.measure(book.step(t, step_s), t, step.total_seconds()))
                ts += step
            for i in range(0, len(batch), 500):
                post(args.url, token, batch[i:i + 500])
            print(f"Backfilled {len(batch)} measurements ({args.backfill} h)")

        print(f"Sending a measurement every {args.interval} s to {args.url} (Ctrl+C to stop)")
        while True:
            book.maybe_reload()
            ts = pd.Timestamp(datetime.now(timezone.utc))
            m = cell.measure(book.step(ts, step_s), ts, args.interval)
            post(args.url, token, m)
            local = ts.tz_convert(cfg.get("general", {}).get("timezone", "Europe/Budapest"))
            print(f"{local:%H:%M:%S}  PV {m['pv_w']:6.1f} W  wind {m['wind_w']:5.1f} W  load {m['load_w']:5.1f} W  "
                  f"grid {m['grid_w']:5.1f} W  battery {m['battery_w']:6.1f} W  curtailed {m['curtailed_w']:5.1f} W  "
                  f"SoC {m['soc'] * 100:5.1f} %", flush=True)
            time.sleep(args.interval)
    except urllib.error.HTTPError as e:
        print(f"The dashboard refused the measurement ({e.code}): {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        print(f"Cannot reach the dashboard at {args.url}: {e.reason}. Is it running?")
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
