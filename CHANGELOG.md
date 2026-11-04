# Changelog

## 1.2.3 (2026-11-04)

Simulation of any load profile and reproduction of the paper, released together; the code and results of 1.1.0
are unchanged. `CITATION.cff`: version 1.2.3 and the authors' departments.

**Simulation of any load profile** (`app/own/`)

- Upload and check of a load file (15/30/60 min, kW, W or kWh, time zones and daylight saving, gaps; days outside
  the data filled from the profile or left out), a sample CSV export, sizing in the site's units, and a closed-loop
  simulation of the controllers on chosen months (October 2025 – September 2026) or the September 2026 test period.
- The forecast is retrained before every month (walk-forward, `prep_bundle.py`, cached per month); quick mode runs
  DET, DET-BO, DET-BO re-sized monthly from r_E and UA-MPC with the reference weights, screening mode gives r_E and
  Γ. Report with costs and energies in the site's units, protection, r_E and Γ.
- After every run: figures in the style and layout of the article's figures, computed from the run (load profile, monthly energy, net cost, protection by SoC edge, Γ against the protection difference, monthly saving and protection, UA-MPC minus each reference with a paired daily bootstrap interval, r_E and Γ, SoC; PNG and PDF) and downloads: the tables as
  CSV, every 15-min step (`steps.csv`) and every MPC solve with its forecast, limits, first planned step, planned SoC
  and solver result (`mpc_solves.csv`), all in one `results.zip` (`app/own/exports.py`, `app/own/figures.py`).
- Runs wait in a queue and run one after another (`OWN_PARALLEL`, default 1); a queued run can be removed, a running
  one stopped and resumed, finished ones deleted; "Use these settings" fills the form from an earlier run.
- Reference check (`app/own/reference.py`): with the reference data set (the button, or its CSV uploaded unchanged at any
  scale) and the reference settings, every run is compared with the stored result of the same set-up.

**Reproduction of the paper**

- Regenerates the paper's numbers, tables and figures from the result files and checks them against the paper:
  121 values of the text (each with its source), 813 table cells, 18 figures pixel by pixel; with the forecast
  models also the calibration values and single re-runs of stored replays, compared with the stored results.
  Command line `python -m app.reproduce`; in the web interface the tab "Paper check", run only on request.
- `app/matplotlibrc`: fixed text rendering of the figure scripts (FreeType hinting), so that every platform draws
  the same pixels; without it the figures differed by 3–5 % on the GitHub runners.

**Package**

- Web interface `python -m app.server`: opens on the simulation; `Dockerfile`, `.dockerignore` for a container.
- `.github/workflows/reproduce.yml`: unit tests (`app/tests/`) and the check of the paper on every push, also in the
  Docker image, with what differs shown as annotations on the run page (`app/tools/ci_annotations.py`).
- `docs/user_guide.md` (Hungarian) and `docs/user_guide_en.md` (English), with screenshots in `docs/img/guide/`;
  `docs/settings.md` / `docs/settings_en.md`: what every simulation setting means and changes;
  `README.md`: sections "Reproducing the paper" and "Simulating any load profile".

## 1.1.0 (2026-10-31)

Additional analyses for the manuscript; the code and results of 1.0.0 are unchanged.

- `experiments/extended/`, `results/extended/`: sensitivity runs (solver, load-forecast error, renewable bias,
  channel κ), conditional calibration and tail diagnostics, pre-registered tests of the ratio (new cells, error
  correlation, back-off sweep) with their records, prospective back-off sizing (T1) and the back-off sweep of the
  second load case (T2) with their record, tariff variants, seasonal re-selection, benchmark of r_E against
  storage-sizing indicators, the Γ of the UA-MPC margin, and the scripts of the new figures (including the
  summary figure split into cost and protection and a column-width Γ figure).
- `experiments/extended/link_tree.sh` builds the working tree the scripts expect.

## 1.0.0 (2026-10-29)

First release with the manuscript.
