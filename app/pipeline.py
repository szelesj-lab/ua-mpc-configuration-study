"""Regenerate the paper's figures, tables and numbers from the shipped result files.

Nothing here re-runs a controller: every step reads the CSV/JSON files under results/ and writes into out/.
The steps are the scripts of the package; this module only orders them and records what happened.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("RESULTS", PKG / "results"))
OUT = Path(os.environ.get("OUT", PKG / "out"))
WORK = OUT / "work"            # working tree for the scripts of experiments/extended (built per run)
LOG = OUT / "run_log.jsonl"


def _run(cmd: list[str], env: dict | None = None, cwd: Path | None = None) -> tuple[bool, str, float]:
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd or PKG, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:], time.time() - t0


def _log(entry: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def build_work_tree() -> Path:
    """Working tree of experiments/extended with links to the shipped results.

    The folder the figure scripts write their data into (rev3/figs) is a copy, so the shipped results stay untouched.
    """
    if WORK.exists():
        shutil.rmtree(WORK)
    ok, out, _ = _run(["bash", str(PKG / "experiments/extended/link_tree.sh")], env={"UA_WORK": str(WORK)})
    if not ok:
        raise RuntimeError("link_tree.sh failed:\n" + out)
    figs = WORK / "rev3" / "figs"
    if figs.is_symlink():
        target = figs.resolve(); figs.unlink(); shutil.copytree(target, figs)
    (WORK / "rev2" / "work" / "figs").mkdir(parents=True, exist_ok=True)
    return WORK


# Each step: name, what it produces, command. Commands run from the package root.
def steps() -> list[dict]:
    py = sys.executable
    w = {"UA_WORK": str(WORK), "OUT": str(OUT), "RESULTS": str(RESULTS)}
    ext = "experiments/extended"
    return [
        {"id": "base", "title": "Numbers, tables and Figs. 2-4, 6-9, 11-12 (experiments/analysis)",
         "cmd": ["bash", "experiments/analysis/run_analysis.sh"], "env": w},
        {"id": "ratio_figs", "title": "Data of the Γ and summary figures, from the raw runs",
         "cmd": [py, f"{ext}/preregistered/figures_ratio_tests.py"], "env": w, "needs_work": True},
        {"id": "fig_split", "title": "Figs. 13-14 (summary of both tests)",
         "cmd": [py, f"{ext}/figures/fig15_split.py"], "env": w, "needs_work": True},
        {"id": "fig_gamma", "title": "Fig. 10 (Γ test)", "cmd": [py, f"{ext}/figures/fig16_col.py"], "env": w, "needs_work": True},
        {"id": "fig_cov", "title": "Fig. 1 (data coverage)", "cmd": [py, f"{ext}/figures/fig01_coverage.py"], "env": w, "needs_work": True},
        {"id": "fig_arch", "title": "Fig. 5 (architecture)", "cmd": [py, f"{ext}/figures/fig_architecture.py"], "env": w, "needs_work": True},
        {"id": "fig_ga", "title": "Graphical abstract", "cmd": [py, f"{ext}/figures/graphical_abstract.py"], "env": w, "needs_work": True},
    ]


def collect_figures() -> None:
    """Copy the figures written into the working tree next to those of the base step (out/figures)."""
    dst = OUT / "figures"; dst.mkdir(parents=True, exist_ok=True)
    for src in (WORK / "rev2" / "work" / "figs").glob("*.p*"):
        shutil.copy(src, dst / src.name)


def run(only: list[str] | None = None, progress=None) -> list[dict]:
    """Run the steps (all, or those in `only`); returns one record per step and appends it to out/run_log.jsonl."""
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [s for s in steps() if not only or s["id"] in only]
    if any(s.get("needs_work") for s in todo):
        build_work_tree()
    records = []
    for i, s in enumerate(todo):
        if progress:
            progress(i, len(todo), s["title"])
        ok, out, dt = _run(s["cmd"], env=s["env"])
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "step": s["id"], "title": s["title"], "ok": ok,
               "seconds": round(dt, 1), "tail": out[-1500:]}
        records.append(rec); _log(rec)
    if any(s.get("needs_work") for s in todo):
        collect_figures()
    if progress:
        progress(len(todo), len(todo), "done")
    return records
