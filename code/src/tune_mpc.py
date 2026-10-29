"""Bayesian optimisation of the MPC weights on closed-loop replays.

Each trial runs the controller with one set of weights over a few replay episodes taken from the
validation days of the held-out period (replay.py) and records several KPIs.

Two modes (tuning.mode):

* "pareto" (default) - multi-objective. The KPIs in tuning.objectives (default: net cost, SoC
  violation, curtailed share of the renewable energy) are minimised together with ParEGO: every
  trial draws random weights for the objectives, scalarises the normalised KPIs with the augmented
  Chebyshev function and lets a Gaussian process propose the next point by expected improvement.
  The result is a Pareto front - the set of weight settings that cannot be improved in one KPI
  without losing in another - with four named operating points:
      cheapest  lowest net cost
      safest    lowest SoC violation (then lowest cost)
      greenest  lowest curtailment (then lowest cost)
      balanced  closest to the ideal point in normalised KPI space
* "single" - one objective: cost - value of the stored energy + violation penalty.

    python src/tune_mpc.py --controller ua                       # Pareto front of the UA-MPC
    python src/tune_mpc.py --controller det                      # same budget for the deterministic MPC
    python src/tune_mpc.py --controller det_bo                   # ... and for the constant back-off baseline
    python src/tune_mpc.py --controller ua --select safest       # which point replay.py --tuned uses
    python src/tune_mpc.py --controller ua --apply --preset balanced   # write it to dataConfig.json
    python src/tune_mpc.py --controller ua --set SoC.E_capacity=72     # tune for another scenario

The search space is tuning.params_<controller> in dataConfig.json:
    "weights.w_x": [low, high, "log" | "lin"], ...
Results in results/tuning/: <controller>_trials.csv, <controller>_pareto.json (+ .png) and
<controller>_best.json (the selected point, read by replay.py --tuned).
"""

import argparse
import json
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import norm, qmc
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from replay import (
    CONTROLLERS, TUNING_DIR, build_data, metrics, named_periods, parse_sets, replay_settings, run_many, with_overrides,
)
from settings import CONFIG_PATH, load_config

# KPIs that can be objectives (all minimised)
OBJECTIVES = {
    "net_cost_eur": "net cost (€)",
    "violation_pp_h": "SoC violation (pp·h)",
    "curtailed_pct": "curtailed renewable (%)",
    "cycles_per_day": "battery cycles per day",
    "grid_kwh": "grid energy (kWh)",
    "peak_grid_w": "grid peak (W)",
    # battery protection (plant "bms"): hours in which the BMS cut charging or discharging, and the extra
    # grid cost of the discharge it cut
    "protection_h": "battery protection (h)",
    "extra_grid_eur": "extra grid cost of protection (€)",
    "objective": "single objective (€)",
}
PRESETS = ("cheapest", "safest", "greenest", "balanced")
# SoC violations below this (pp·h) are numerical noise (plans are stored with 0.01 W resolution)
VIOLATION_NOISE_PP_H = 0.01


def objective_vector(res, objectives):
    """The KPIs that are minimised; violation noise counts as zero."""
    out = []
    for o in objectives:
        v = res[o]
        if o == "violation_pp_h" and v < VIOLATION_NOISE_PP_H:
            v = 0.0
        out.append(v)
    return out


def tuning_settings(cfg, controller):
    t = cfg.get("tuning", {})
    params = t.get(f"params_{controller}")
    if not params:
        raise SystemExit(f"tuning.params_{controller} is missing in the configuration")
    objectives = list(t.get("objectives", ["net_cost_eur", "violation_pp_h", "curtailed_pct"]))
    unknown = [o for o in objectives if o not in OBJECTIVES]
    if unknown:
        raise SystemExit(f"unknown tuning objective(s) {unknown}; choose from {', '.join(OBJECTIVES)}")
    return {
        "mode": t.get("mode", "pareto"),
        "objectives": objectives,
        "trials": int(t.get("trials", 24)),
        "initial_points": int(t.get("initial_points", 8)),
        "episodes": int(t.get("episodes", 4)),
        "episode_days": float(t.get("episode_days", 3)),
        "random_state": int(t.get("random_state", 42)),
        "params": params,
    }


class Space:
    """Maps the unit cube to weights and back ("log" ranges are searched on a log scale)."""

    def __init__(self, params):
        self.names = list(params)
        self.lo = np.array([float(params[n][0]) for n in self.names])
        self.hi = np.array([float(params[n][1]) for n in self.names])
        self.log = np.array([len(params[n]) > 2 and params[n][2] == "log" for n in self.names])
        if np.any(self.log & (self.lo <= 0)):
            raise SystemExit("log ranges need a positive lower bound")

    def _t(self, v):
        v = np.asarray(v, dtype=float)
        return np.where(self.log, np.log(np.where(self.log, np.clip(v, 1e-300, None), 1.0)), v)

    def to_values(self, u):
        lo_t, hi_t = self._t(self.lo), self._t(self.hi)
        t = lo_t + np.clip(u, 0, 1) * (hi_t - lo_t)
        return np.where(self.log, np.exp(np.where(self.log, t, 0.0)), t)

    def to_unit(self, v):
        lo_t, hi_t = self._t(self.lo), self._t(self.hi)
        return np.clip((self._t(v) - lo_t) / (hi_t - lo_t), 0, 1)

    def overrides(self, values):
        out = {}
        for name, v in zip(self.names, values):
            section, key = name.split(".", 1)
            out.setdefault(section, {})[key] = float(v)
        return out

    def current(self, cfg):
        vals = []
        for name, lo, hi in zip(self.names, self.lo, self.hi):
            section, key = name.split(".", 1)
            vals.append(float(np.clip(cfg.get(section, {}).get(key, (lo + hi) / 2), lo, hi)))
        return np.array(vals)


def episodes(data, cfg, n, days):
    """n windows of `days` days, evenly spread over the tuning period."""
    v0, v1 = named_periods(data, cfg)["tuning"]
    length = pd.Timedelta(days=days)
    span = (v1 - v0) - length
    if span < pd.Timedelta(0):
        raise SystemExit("tuning.episode_days is longer than the tuning period")
    starts = [v0 + span * (i / max(n - 1, 1)) for i in range(n)] if n > 1 else [v0]
    step = pd.Timedelta(seconds=float(cfg["general"]["deltaT"]))
    return [(s.floor(step), s.floor(step) + length) for s in starts]


def evaluate(data, cfg, controller, windows, workers):
    """KPIs summed (or recomputed) over all episodes."""
    jobs = [(data, cfg, controller, a, b) for a, b in windows]
    parts = [metrics(df, stats, cfg) for _, df, stats in run_many(jobs, workers)]
    keys = ["objective", "net_cost_eur", "total_eur", "grid_eur", "wear_eur", "stored_value_eur", "grid_kwh",
            "renewable_kwh", "curtailed_kwh", "throughput_kwh", "h_below_soc_min", "h_above_soc_max",
            "violation_pp_h", "equivalent_full_cycles", "days"]
    out = {k: float(sum(p[k] for p in parts)) for k in keys}
    out["curtailed_pct"] = 100 * out["curtailed_kwh"] / out["renewable_kwh"] if out["renewable_kwh"] > 0 else 0.0
    out["renewable_use_pct"] = 100 - out["curtailed_pct"]
    out["cycles_per_day"] = out["equivalent_full_cycles"] / out["days"] if out["days"] > 0 else 0.0
    out["peak_grid_w"] = float(max(p["peak_grid_w"] for p in parts))
    out["max_violation_pp"] = float(max(p["max_violation_pp"] for p in parts))
    for k in ("protection_steps", "protection_h", "undelivered_discharge_kwh", "undelivered_charge_kwh", "extra_grid_eur"):
        if all(k in p for p in parts):
            out[k] = float(sum(p[k] for p in parts))
    out["min_soc"] = float(min(p["min_soc"] for p in parts))
    return out


# ---------------------------------------------------------------- multi-objective helpers

def pareto_mask(F):
    """True for the rows of F (n x m, minimised) that no other row dominates."""
    F = np.asarray(F, dtype=float)
    keep = np.ones(len(F), dtype=bool)
    for i in range(len(F)):
        dominated = np.all(F <= F[i], axis=1) & np.any(F < F[i], axis=1)
        keep[i] = not dominated.any()
    return keep


def normalise(F):
    """Scale every objective to [0, 1] over the observed values (a constant objective becomes 0)."""
    F = np.asarray(F, dtype=float)
    lo, hi = F.min(axis=0), F.max(axis=0)
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    return (F - lo) / span


def chebyshev(Fn, w, rho=0.05):
    """Augmented Chebyshev scalarisation used by ParEGO (minimised)."""
    return np.max(Fn * w, axis=1) + rho * np.sum(Fn * w, axis=1)


def presets(F, objectives):
    """Indices (into F) of the named operating points on the Pareto front."""
    F = np.asarray(F, dtype=float)
    front = np.flatnonzero(pareto_mask(F))
    Fn = normalise(F)
    col = {o: i for i, o in enumerate(objectives)}

    def lexmin(first, then="net_cost_eur"):
        if first not in col:
            return None
        cand = front[np.isclose(F[front, col[first]], F[front, col[first]].min())]
        if then in col and len(cand) > 1:
            cand = cand[np.argsort(F[cand, col[then]])]
        return int(cand[0])

    out = {
        "cheapest": lexmin("net_cost_eur", "violation_pp_h"),
        "safest": lexmin("violation_pp_h"),
        "greenest": lexmin("curtailed_pct"),
        "balanced": int(front[np.argmin(np.linalg.norm(Fn[front], axis=1))]),
    }
    return {k: v for k, v in out.items() if v is not None}


def expected_improvement(gp, X, best):
    mu, sd = gp.predict(X, return_std=True)
    sd = np.maximum(sd, 1e-12)
    imp = best - mu  # minimisation
    z = imp / sd
    return imp * norm.cdf(z) + sd * norm.pdf(z)


def propose(gp, U, y, rng, n_random=4000):
    best = y.min()
    cand = rng.random((n_random, U.shape[1]))
    # also search around the best points found so far
    top = U[np.argsort(y)[:3]]
    local = np.clip(np.repeat(top, 300, axis=0) + rng.normal(0, 0.08, (900, U.shape[1])), 0, 1)
    cand = np.vstack([cand, local])
    ei = expected_improvement(gp, cand, best)
    return cand[int(np.argmax(ei))]


def _fmt_values(names, values):
    return " ".join("{}={:.3g}".format(n.split(".")[-1], v) for n, v in zip(names, values))


def _write_config(overrides, label):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    for section, values in overrides.items():
        raw.setdefault(section, {}).update({k: float(f"{v:.6g}") for k, v in values.items()})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(f"Stored {label} in {CONFIG_PATH.name}: {overrides}")


def _select(controller, preset):
    """Make one Pareto point the 'best' one that replay.py --tuned and --apply use."""
    pareto = json.loads((TUNING_DIR / f"{controller}_pareto.json").read_text(encoding="utf-8"))
    if preset not in pareto["presets"]:
        raise SystemExit(f"preset '{preset}' is not available; choose from {', '.join(pareto['presets'])}")
    point = pareto["points"][pareto["presets"][preset]]
    best = {"controller": controller, "mode": "pareto", "preset": preset, "overrides": point["overrides"],
            "kpis": point["kpis"], "scenario": pareto.get("scenario", {})}
    (TUNING_DIR / f"{controller}_best.json").write_text(json.dumps(best, indent=2, default=float), encoding="utf-8")
    return best


def _plot_front(path, F, mask, objectives, preset_idx, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    pairs = [(i, j) for i in range(len(objectives)) for j in range(i + 1, len(objectives))]
    fig, axes = plt.subplots(1, len(pairs), figsize=(4.6 * len(pairs), 4), squeeze=False)
    for ax, (i, j) in zip(axes[0], pairs):
        ax.scatter(F[~mask, i], F[~mask, j], s=18, color="#b8bcc2", label="dominated")
        ax.scatter(F[mask, i], F[mask, j], s=30, color="#2f6f4f", label="Pareto front")
        for name, k in preset_idx.items():
            ax.annotate(name, (F[k, i], F[k, j]), fontsize=8, xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel(OBJECTIVES[objectives[i]])
        ax.set_ylabel(OBJECTIVES[objectives[j]])
        ax.grid(alpha=0.25)
    axes[0][0].legend(fontsize=8)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Bayesian optimisation of the MPC weights on replays.")
    parser.add_argument("--controller", default="ua", choices=[k for k, v in CONTROLLERS.items() if v["kind"] == "mpc"])
    parser.add_argument("--mode", choices=["pareto", "single"], default=None, help="override tuning.mode")
    parser.add_argument("--trials", type=int, default=0, help="override tuning.trials")
    parser.add_argument("--episodes", type=int, default=0, help="override tuning.episodes")
    parser.add_argument("--episode-days", type=float, default=0, help="override tuning.episode_days")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--resume", action="store_true",
                        help="continue from <controller>_trials.csv if it was made with the same settings")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                        help="tune for a modified scenario, e.g. --set SoC.E_capacity=72")
    parser.add_argument("--select", choices=PRESETS, help="choose the Pareto point that replay.py --tuned uses")
    parser.add_argument("--apply", action="store_true", help="write the selected weights to dataConfig.json")
    parser.add_argument("--preset", choices=PRESETS, help="with --apply: which Pareto point to write")
    args = parser.parse_args()

    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    best_path = TUNING_DIR / f"{args.controller}_best.json"
    if args.select:
        best = _select(args.controller, args.select)
        print(f"'{args.select}' point of '{args.controller}' selected: {best['overrides']}")
        return
    if args.apply:
        best = _select(args.controller, args.preset) if args.preset else json.loads(best_path.read_text(encoding="utf-8"))
        _write_config(best["overrides"], f"the tuned weights of '{args.controller}' ({best.get('preset', 'single')})")
        return

    scenario = parse_sets(args.set)
    cfg = with_overrides(load_config(), scenario)
    ts = tuning_settings(cfg, args.controller)
    for key, val in (("mode", args.mode), ("trials", args.trials), ("episodes", args.episodes),
                     ("episode_days", args.episode_days)):
        if val:
            ts[key] = val
    objectives = ts["objectives"] if ts["mode"] == "pareto" else ["objective"]
    workers = args.workers or replay_settings(cfg)["workers"]
    space = Space(ts["params"])
    data = build_data(cfg)
    windows = episodes(data, cfg, ts["episodes"], ts["episode_days"])
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    print(f"Tuning '{args.controller}' ({CONTROLLERS[args.controller]['label']}), mode {ts['mode']}: "
          f"{len(space.names)} weights, objectives {', '.join(objectives)}, {ts['trials']} trials, "
          f"{len(windows)} episodes of {ts['episode_days']:g} days{f', scenario {scenario}' if scenario else ''}:",
          flush=True)
    for a, b in windows:
        print(f"  {a.tz_convert(tz):%Y-%m-%d %H:%M} – {b.tz_convert(tz):%Y-%m-%d %H:%M}")

    rng = np.random.default_rng(ts["random_state"])
    lhs = qmc.LatinHypercube(d=len(space.names), seed=ts["random_state"]).random(max(ts["initial_points"], 1))
    U, F, rows = [], [], []
    gp = GaussianProcessRegressor(
        kernel=ConstantKernel(1.0) * Matern(length_scale=np.full(len(space.names), 0.3), nu=2.5,
                                            length_scale_bounds=(1e-2, 10)) + WhiteKernel(1e-3, (1e-8, 1e-1)),
        normalize_y=True, n_restarts_optimizer=3, random_state=ts["random_state"],
    )
    trials_path = TUNING_DIR / f"{args.controller}_trials.csv"
    meta_path = TUNING_DIR / f"{args.controller}_trials.meta.json"
    meta = {"episodes": [[a.isoformat(), b.isoformat()] for a, b in windows], "scenario": scenario,
            "objectives": objectives, "params": space.names}
    same_run = meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")) == json.loads(json.dumps(meta))
    if args.resume and trials_path.exists() and not same_run:
        print(f"not resuming: {trials_path.name} was made with other episodes or settings, starting over", flush=True)
    if args.resume and trials_path.exists() and same_run:
        prev = pd.read_csv(trials_path)
        if list(prev.columns[3:3 + len(space.names)]) == space.names and len(prev):
            for rec in prev.drop(columns=["pareto"], errors="ignore").to_dict("records"):
                rec = {k: (None if isinstance(v, float) and np.isnan(v) and k == "scalarisation_weights" else v)
                       for k, v in rec.items()}
                rows.append(rec)
                U.append(space.to_unit(np.array([rec[n] for n in space.names], dtype=float)))
                F.append(objective_vector(rec, objectives))
            # a fresh, reproducible stream for the remaining trials
            rng = np.random.default_rng(ts["random_state"] + len(rows))
            print(f"resuming after {len(rows)} trials from {trials_path}", flush=True)
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for trial in range(len(rows), ts["trials"]):
        weights = None
        if trial == 0:
            u, source = space.to_unit(space.current(cfg)), "current config"
        elif trial <= ts["initial_points"]:
            u, source = lhs[trial - 1], "initial design"
        else:
            Fa = np.array(F)
            if len(objectives) > 1:
                weights = rng.dirichlet(np.ones(len(objectives)))
                y = chebyshev(normalise(Fa), weights)
            else:
                y = Fa[:, 0]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                gp.fit(np.array(U), y)
            u, source = propose(gp, np.array(U), y, rng), "expected improvement"
        values = space.to_values(u)
        t0 = time.perf_counter()
        res = evaluate(data, with_overrides(cfg, space.overrides(values)), args.controller, windows, workers)
        U.append(u)
        F.append(objective_vector(res, objectives))
        rows.append({"trial": trial, "source": source,
                     "scalarisation_weights": None if weights is None else " ".join(f"{w:.2f}" for w in weights),
                     **dict(zip(space.names, values)), **res, "seconds": time.perf_counter() - t0})
        trials = pd.DataFrame(rows)
        trials["pareto"] = pareto_mask(np.array(F))
        trials.to_csv(trials_path, index=False)
        kpis = " ".join(f"{o}={res[o]:.4g}" for o in objectives)
        print(f"trial {trial + 1:>3}/{ts['trials']} {source:<20} {kpis} · front {int(trials['pareto'].sum())} "
              f"· {_fmt_values(space.names, values)} · {time.perf_counter() - t0:.0f} s", flush=True)

    Fa = np.array(F)
    mask = pareto_mask(Fa)
    kpi_keys = ["net_cost_eur", "violation_pp_h", "max_violation_pp", "curtailed_pct", "renewable_use_pct",
                "cycles_per_day", "grid_kwh", "peak_grid_w", "total_eur", "objective", "min_soc"]
    kpi_keys += [k for k in ("protection_h", "extra_grid_eur") if k in rows[0]]
    points = [{"trial": int(i), "overrides": space.overrides(space.to_values(U[i])),
               "kpis": {k: rows[i][k] for k in kpi_keys}} for i in np.flatnonzero(mask)]
    index_of = {int(i): n for n, i in enumerate(np.flatnonzero(mask))}
    if len(objectives) > 1:
        named = presets(Fa, objectives)
    else:
        named = {"cheapest": int(np.argmin(Fa[:, 0]))}
    pareto = {
        "controller": args.controller, "mode": ts["mode"], "objectives": objectives, "scenario": scenario,
        "episodes": [[a.isoformat(), b.isoformat()] for a, b in windows], "trials": len(F),
        "baseline": {"overrides": space.overrides(space.to_values(U[0])), "kpis": {k: rows[0][k] for k in kpi_keys}},
        "points": points, "presets": {k: index_of[v] for k, v in named.items()},
    }
    (TUNING_DIR / f"{args.controller}_pareto.json").write_text(json.dumps(pareto, indent=2, default=float), encoding="utf-8")
    if len(objectives) > 1:
        _plot_front(TUNING_DIR / f"{args.controller}_pareto.png", Fa, mask, objectives, named,
                    f"{CONTROLLERS[args.controller]['label']}: {len(F)} trials, {int(mask.sum())} on the Pareto front")
    best = _select(args.controller, "balanced" if "balanced" in named else "cheapest")

    print(f"\nPareto front: {int(mask.sum())} of {len(F)} trials. Named points:")
    for name, i in named.items():
        k = rows[i]
        print(f"  {name:<9} trial {i:>3}: net cost {k['net_cost_eur']:.4f} €, violation {k['violation_pp_h']:.2f} pp·h, "
              f"curtailed {k['curtailed_pct']:.1f} %, {k['cycles_per_day']:.2f} cycles/day")
    b = rows[0]
    print(f"  current   trial   0: net cost {b['net_cost_eur']:.4f} €, violation {b['violation_pp_h']:.2f} pp·h, "
          f"curtailed {b['curtailed_pct']:.1f} %, {b['cycles_per_day']:.2f} cycles/day")
    print(f"Selected for replay.py --tuned: {best['preset']}. Change with --select, write to the config with --apply.")


if __name__ == "__main__":
    main()
