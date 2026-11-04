"""Command line: regenerate everything from the result files and check the paper.

    python -m app.reproduce            # all steps, then the checks; writes out/report.md and out/report.json
    python -m app.reproduce --check    # only the checks (after an earlier run)

Checks: the values of the text (app/catalogue.py), every numeric cell of the tables (app/tables.py) and every
figure against the paper's figure (app/figcheck.py). Values that need the trained forecast models are listed as
such; they are checked after the forecast calibration has been re-run (web interface, Experiments).
Exit code 0 when everything that can be checked from the result files is reproduced.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time

from app import catalogue, figcheck, pipeline, tables


def summary(values, tabs, figs) -> dict:
    checked = [v for v in values if v["ok"] is not None]
    return {"values_ok": sum(v["ok"] for v in checked), "values": len(checked),
            "values_need_models": sum(v["ok"] is None for v in values),
            "cells_ok": sum(t["cells_ok"] for t in tabs), "cells": sum(t["cells"] for t in tabs),
            "figures_ok": sum(f["ok"] for f in figs), "figures": len(figs)}


def run_checks() -> dict:
    values, tabs, figs = catalogue.evaluate(), tables.check_tables(), figcheck.check_figures()
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary(values, tabs, figs),
            "values": values, "tables": tabs, "figures": figs}


def all_ok(rep: dict, steps: list[dict]) -> bool:
    s = rep["summary"]
    return s["values_ok"] == s["values"] and s["cells_ok"] == s["cells"] and s["figures_ok"] == s["figures"] and all(x["ok"] for x in steps)


def write_report(steps: list[dict], rep: dict) -> str:
    s = rep["summary"]
    L = ["# Reproduction report", "", f"Generated {rep['time']} from the result files in `results/`.", "",
         f"- Values of the text: {s['values_ok']} of {s['values']} reproduced"
         + (f" ({s['values_need_models']} more need the forecast models)" if s["values_need_models"] else ""),
         f"- Table cells: {s['cells_ok']} of {s['cells']} reproduced",
         f"- Figures: {s['figures_ok']} of {s['figures']} identical to the paper", ""]
    if steps:
        L += ["## Steps", "", "| Step | Result | Seconds |", "|---|---|---|"]
        L += [f"| {x['title']} | {'ok' if x['ok'] else 'FAILED'} | {x['seconds']} |" for x in steps] + [""]
    L += ["## Values of the text", "", "| Section | Statement | Paper | Reproduced | | Source |", "|---|---|---|---|---|---|"]
    for v in rep["values"]:
        mark = "✓" if v["ok"] else ("–" if v["ok"] is None else "✗ " + v["error"])
        L.append(f"| {v['section']} | {v['statement']} | {v['paper']} | {v['reproduced']} | {mark} | {v['source']} |")
    L += ["", "## Tables", "", "| Table | Cells reproduced |", "|---|---|"]
    L += [f"| {t['no']} {t['title']} | {t['cells_ok']} of {t['cells']}{' ' + t['error'] if t['error'] else ''} |" for t in rep["tables"]]
    bad = [(t, r, c) for t in rep["tables"] for r in t["rows"] for c in r["cells"] if not c["ok"]]
    if bad:
        L += ["", "Cells that differ:", ""] + [f"- {t['no']}, {r['label'] or 'row'}: paper {c['paper']}, reproduced {c['reproduced']}" for t, r, c in bad]
    L += ["", "## Figures", "", "| Figure | Identical to the paper | Pixels differing |", "|---|---|---|"]
    L += [f"| {f['no']} {f['title']} | {'✓' if f['ok'] else '✗ ' + f['note']} | {f['differing_pct'] if f['differing_pct'] is not None else '–'} % |" for f in rep["figures"]]
    L += ["", "Numbers quoted from other studies (Section 5.7) are not results of this package and are not checked.", ""]
    text = "\n".join(L)
    out = pipeline.OUT
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "report.json").write_text(json.dumps({"steps": steps, **rep}, indent=1), encoding="utf-8")
    hist = out / "reports"; hist.mkdir(exist_ok=True)  # every report is kept, for comparing runs
    shutil.copy(out / "report.json", hist / f"report_{time.strftime('%Y%m%d_%H%M%S')}.json")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only compare, do not regenerate")
    a = ap.parse_args()
    steps = [] if a.check else pipeline.run(progress=lambda i, n, t: print(f"[{i}/{n}] {t}", flush=True))
    rep = run_checks()
    print(write_report(steps, rep))
    return 0 if all_ok(rep, steps) else 1


if __name__ == "__main__":
    sys.exit(main())
