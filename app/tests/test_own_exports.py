"""Exports of a run: steps and MPC solves in the site's units, tables, figures and the zip."""
import json
import zipfile

import pandas as pd

from app.own import exports


def _job(tmp_path):
    d = tmp_path / "job"; (d / "runs" / "B").mkdir(parents=True)
    t = pd.date_range("2025-11-01", periods=8, freq="15min", tz="UTC")
    steps = pd.DataFrame({"t": t, "soc": 0.5, "demand_w": 10.0, "grid_w": 4.0, "discharge_w": 6.0, "charge_w": 0.0,
                          "renewable_w": 1.0, "curtailed_w": 0.0, "price_eur_wh": 1e-4, "grid_eur": 1e-4, "wear_eur": 1e-6,
                          "cut_discharge_w": 0.0, "cut_charge_w": 0.0})
    for c in ("det", "det_bo"):
        steps.to_csv(d / "runs" / "B" / f"{c}_2025-11.csv", index=False)
    pd.DataFrame({"t": t[::4], "soc_start": 0.5, "plan_discharge_w": 6.0, "demand_w": 10.0, "success": True}).to_csv(
        d / "runs" / "B" / "det_2025-11_mpc.csv", index=False)
    (d / "report.md").write_text("# r"); (d / "report.json").write_text("{}")
    return d


def test_units_and_files(tmp_path):
    d = _job(tmp_path)
    f = 0.001   # cell W = site W x f  ->  10 W cell = 10 kW site
    s = exports.steps(d, f)
    assert len(s) == 16 and set(s.controller) == {"det", "det_bo"} and set(s.month) == {"2025-11"}
    assert abs(s.demand_kw.iloc[0] - 10.0) < 1e-9 and abs(s.grid_eur.iloc[0] - 0.1) < 1e-9 and s.price_eur_mwh.iloc[0] == 100.0
    m = exports.solves(d, f)
    assert len(m) == 2 and abs(m.plan_discharge_kw.iloc[0] - 6.0) < 1e-9 and m.controller.iloc[0] == "det"
    rep = {"cell": {"factor": f}, "totals": [{"config": "B", "controller": "det", "net_cost_eur": 1.0, "protection_h": 0.0,
                                               "grid_kwh": 1.0, "curtailed_kwh": 0.0}],
           "monthly": [], "gamma": [{"config": "B", "month": "2025-11", "r_E": 0.05, "Gamma_ref_b": 1.3}]}
    files = exports.write(d, rep)
    assert {"steps.csv", "mpc_solves.csv", "totals.csv", "results.zip"} <= set(files)
    assert any(x.startswith("figures/") for x in files)
    names = zipfile.ZipFile(d / "export" / "results.zip").namelist()
    assert "steps.csv" in names and "report.md" in names and any(n.startswith("figures/") for n in names)
    json.dumps(files)
