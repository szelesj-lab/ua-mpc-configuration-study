# Changelog

## 1.2.1 (2026-11-04)

Reproduction of the paper and simulation of any load profile, released together; the code and results of 1.1.0
are unchanged. `CITATION.cff`: version 1.2.1 and the authors' departments.

**Reproduction of the paper**

- `app/`: regenerates the paper's numbers, tables and figures from the result files and checks them against the
  paper: 121 values of the text (each with its source), 813 table cells, 18 figures pixel by pixel; with the
  forecast models also the calibration values and single re-runs of stored replays, compared with the stored
  results. Web interface (`python -m app.server`) and command-line report (`python -m app.reproduce`).
- `Dockerfile`, `.dockerignore`: the same in a container; `.github/workflows/reproduce.yml`: the check on every push,
  with what differs shown as annotations on the run page (`app/tools/ci_annotations.py`).
- `README.md`: section "Reproducing the paper".

**Simulation of any load profile**

- `app/own/`: upload and check of a load file (15/30/60 min, kW, W or kWh, time zones and daylight saving, gaps;
  days outside the data filled from the profile or left out), sizing in the site's units, and a closed-loop
  simulation of the controllers on chosen months (October 2025 – September 2026) or the September 2026 test period.
  The forecast is retrained before every month (walk-forward, `prep_bundle.py`, cached per month); quick mode runs
  DET, DET-BO, DET-BO re-sized monthly from r_E and UA-MPC with the reference weights, screening mode gives r_E and Γ.
  Report with costs and energies in the site's units, protection, r_E and Γ.
- Reference check (`app/own/reference.py`): with the reference data set and settings, every run is compared with
  the stored result of the same set-up.
- Web interface: tab "Your data" (sample CSV export, upload or reference data set, preview, parameters, live progress with the remaining
  time, resume, report and downloads). `app/tests/`: unit tests of the upload and the mapping, run in the GitHub
  workflow.
- `docs/user_guide.md` (Hungarian) and `docs/user_guide_en.md` (English): user guide of the web interface, with
  screenshots in `docs/img/guide/`, linked from the interface and the README; `README.md`: section "Simulating any load profile".

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
