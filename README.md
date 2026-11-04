# ua-mpc-configuration-study

Simulation package of

> J. Szeles, L. Deszát, A. Kummer, A. Fodor, T. Ruppert: *Configuration-dependent value of uncertainty-aware MPC
> for behind-the-meter batteries in food processing* (manuscript in preparation).
> University of Pannonia, Hungary.

It holds the controller code in the exact version used, the experiment pipelines, every result file, and the
scripts that produce all tables, numbers and figures of the paper.

## The study in short

A simulated laboratory-scale production cell (30 W PV, 20 W wind turbine, 72 Wh battery) is driven by one year of measured
load of a Hungarian meat-processing plant, scaled to the cell (per unit of the annual mean × 10.788 W), Hungarian
day-ahead prices (ENTSO-E) and Gaussian-process (GP) renewable forecasts built from Open-Meteo weather data.
Three controllers are compared in closed-loop replay:

| | Controller | Safety margin on the SoC band |
|---|---|---|
| `det` | deterministic MPC | none |
| `det_bo` | deterministic MPC with a tuned constant back-off | constant `b` |
| `ua` | uncertainty-aware MPC | chance-constrained margin from the calibrated forecast σ, accumulated until the next re-plan |

in three battery configurations (`mpc.architecture`):

| | Configuration | Where a forecast error goes |
|---|---|---|
| A | `renewable_buffer`: renewables only charge the battery | into the SoC (deficit side) |
| B | `self_consumption`: renewables serve the load first, grid charging allowed | into the SoC (both sides) |
| C | `setpoint`: the battery follows its plan, the grid balances | into the grid, not the SoC |

Two evaluations:

- **Summer protocol** (16 June – 29 September 2026): calibration of σ, equal-budget multi-objective Bayesian tuning
  (ParEGO, 30 trials per controller), replay of every Pareto point on a development period, a point-selection rule
  recorded before the test (`results/summer/<X>/final_test/preregistration.json`), and a one-shot final test.
  A decision map varies capacity, forecast-error level and re-planning interval.
- **Walk-forward winter test** (1 October 2025 – 31 March 2026, 181 days): for every month the GP is re-trained on
  the data before the month only, σ is re-calibrated on its held-out tail, and the summer-selected controllers of
  B and C are replayed with the SoC carried over between months. Nothing is tuned on winter data.

A second load case (a synthetic production schedule, configuration A) is in `results/production_schedule`.

## Layout

| Path | Content |
|---|---|
| `code/` | controller code (Kairos) in the version used: MPC, replay, tuning, calibration, GP training; `code/data/` the input data |
| `experiments/make_work.sh` | creates the working copies of the code (`work/A`, `work/B`, `work/C`, `work/W/m1..m6`) |
| `experiments/summer/` | summer pipeline: `run_summer.sh` (tuning → fronts → selection-rule record → decision map → final test), `engine.py`, `stages.py` |
| `experiments/winter/` | walk-forward test: `run_winter.sh`, `train_rewind.py`, `prep_month.py`, `run_rewind.py` |
| `experiments/analysis/` | `run_analysis.sh`: tables, numbers (`make_numbers.py`) and figures (`figures.py`) of the paper from the results |
| `results/summer/<A,B,C>/` | tuning trials and Pareto fronts, development fronts, selection-rule record, final test (summary, paired bootstrap, every step), decision map, run logs |
| `results/winter/` | per configuration, controller and month: step CSVs and metrics; `summary.csv`, `paired_bootstrap.csv`; `calibration/m<i>.json` (cut-off, σ scale, AR(1), out-of-sample coverage) |
| `results/analysis/` | data inventory, period and monthly energies, protection hours by SoC edge, decision-map table; `extended/`: solver convergence, quantile sensitivity of r_E, calibration per channel, stricter-solver re-run of B UA-MPC (`experiments/analysis/solver_sensitivity.py`) |
| `results/production_schedule/` | second load case |
| `experiments/extended/`, `results/extended/` | additional analyses (v1.1.0): sensitivity, tail diagnostics, pre-registered tests, T1/T2, tariffs, seasonal re-selection, indicator benchmark; see `experiments/extended/README.md` |
| `app/` | web interface and command line: simulation of any load profile (`app/own/`, queued runs) and the check of the paper |
| `docs/` | protocol, data description and the user guide of the web interface and the description of the simulation settings (`user_guide.md`, `settings.md` in Hungarian; `user_guide_en.md`, `settings_en.md` in English) |

## Reproducing

Python ≥ 3.10 (results made with 3.13; versions in `requirements-frozen.txt`).

```bash
pip install -r code/requirements.txt
```

**1. Tables, numbers and figures from the shipped results** (minutes, no replay):

```bash
experiments/analysis/run_analysis.sh        # -> out/manuscript/numbers3.tex, out/manuscript/tables/, out/figures/
```

**2. Walk-forward winter test** (about 10 h on 2 cores; trains six GP model pairs):

```bash
experiments/make_work.sh
experiments/winter/run_winter.sh            # -> out/winter/ ; compare with results/winter/
```

**3. Summer protocol** (several days on 2 cores):

```bash
SUMMER_MODELS=/path/to/summer/models experiments/make_work.sh
experiments/summer/run_summer.sh            # -> out/summer/<A,B,C>/
```

`run_summer.sh` writes a new selection-rule record in `out/summer`; the shipped record in `results/summer` is
the one written before the published final test.

To run the analysis on re-run results, point `RESULTS` to a folder with the same layout as `results/`.

### Forecast models

The GP model files are too large for GitHub (about 380 MB for the summer models, 60–250 MB per winter month).
The winter models are re-trained by `run_winter.sh`. The summer models were trained with
`code/src/scicomp_training.py` on the weather data before 16 June 2026 and are attached to the GitHub release v1.2.3 of
this package as `models_summer.zip`; unpack them to `models_summer/` before `make_work.sh` and check them against `docs/models_summer.sha256`. GP fitting uses
random restarts, so models re-trained from scratch give slightly different forecasts.

### Notes

- The ENTSO-E API key is only needed to update the price data and is read from `.env` (never stored in code).
- Every pipeline step is resumable and leaves a marker; an interrupted run continues where it stopped.
- Small numerical differences between machines (BLAS threads) can shift single protection events; the published
  runs used one BLAS thread per process (`OMP_NUM_THREADS=1`).

## Data

See `docs/data.md` and `LICENSE-DATA.md`. The plant load is anonymised (per unit, no absolute values, no meter or
company names); the raw company export is not included.

## Reproducing the paper

[![reproduce](https://github.com/szelesj-lab/ua-mpc-configuration-study/actions/workflows/reproduce.yml/badge.svg)](https://github.com/szelesj-lab/ua-mpc-configuration-study/actions/workflows/reproduce.yml)

The paper can be checked against the shipped result files in under a minute, without re-running a controller:

- every value of the text (121 values; `app/catalogue.py`, each with the file or script it comes from),
- every numeric cell of the tables (813 cells of 17 tables; `app/tables.py`),
- every figure, pixel by pixel against the figure printed in the paper (18 figures; `app/figcheck.py`).

The GitHub workflow `.github/workflows/reproduce.yml` runs the same check on every push, also inside the Docker
image; the badge above shows the result and each run keeps the report as an artifact.

```bash
docker build -t ua-mpc-repro .
docker run --rm -p 8501:8501 ua-mpc-repro             # web interface: http://localhost:8501 (tab Paper check)
docker run --rm ua-mpc-repro python -m app.reproduce   # command line: report on stdout, exit code 0 if all match
```

Without Docker (Linux, macOS or WSL; Python 3.13, `pip install -r requirements-frozen.txt`): `python -m app.server`
or `python -m app.reproduce` from the package root. Outputs go to `out/`: `report.md` and `report.json`, every
earlier report in `reports/`, the run log `run_log.jsonl`, `figures/` and `manuscript/`.

**Re-running experiments.** With the trained summer forecast models (`models_summer.zip`, attached to the GitHub
release) the web interface (tab Paper check → Re-run experiments) also re-runs single experiments: the forecast calibration (the 6 values of Sections 4.2
and 5.1 that need the models) and any stored replay of the final test or the decision map (122 runs, from a few
minutes to about an hour each). It shows the progress and the SoC while the replay runs and compares the result
with the stored one within stated tolerances (net cost 0.1 %, protection 0.5 h).

```bash
unzip models_summer.zip -d models_summer
docker run --rm -p 8501:8501 -v "$PWD/models_summer:/pkg/models_summer" ua-mpc-repro
```

The first re-run builds the work copies of `experiments/make_work.sh` (about 1.2 GB). Numbers quoted from other
studies (Section 5.7) are not results of this package and are not checked. The paper's tables and figures used as
reference were taken from the manuscript with `app/tools/extract_reference.py` and `app/tools/make_reference_figures.py`.

## Simulating any load profile

Step-by-step instructions for the web interface, the file format and the results: **[`docs/user_guide.md`](docs/user_guide.md)**
(Hungarian) and [`docs/user_guide_en.md`](docs/user_guide_en.md) (English); every setting explained in
[`docs/settings.md`](docs/settings.md) / [`docs/settings_en.md`](docs/settings_en.md).

The web interface opens on the **Simulation** tab: it runs the controllers on any load profile with a battery, PV
and wind sized by the user. Any number of runs can be added; they wait in a queue and run one after another (each
can be stopped, resumed, deleted, or used as the starting point of the next run). The same tool chain also reproduces the reference results: run with the reference data set shipped in
the package and the reference settings, it compares every run with the stored result of the same set-up.

1. **Load data.** Upload a CSV with a timestamp and a load column, at 15-, 30- or 60-minute resolution, as average
   power (kW or W) or energy per interval (kWh). A sample file in this format can be exported on the tab (length,
   resolution, unit and mean load to choose). The file is checked (resolution, time
   zone and daylight saving, duplicates, negative values, gaps, zeros, spikes; at least one full week). Gaps up to 2 h
   are interpolated. Or press **Use the reference data set**: the measured plant load shipped in
   `code/data/industrial_load_profile.csv` (described in `docs/data.md`).
2. **System.** Battery capacity and power limit, PV peak power, wind turbine rated power, in the site's units. The
   default keeps the ratios of the reference system to the mean load (battery 6.7 h of mean load, power 2.2×,
   PV 2.8×, wind 1.9×).
3. **Period and mode.** Configurations (A, B, C), controllers, months (October 2025 – September 2026) or the
   September 2026 test period; days the load data do not cover are either filled from the profile or left out.

How the simulation works:

- The site is mapped onto the reference cell by one factor (cell mean load 10.788 W / site mean load); battery, PV
  and wind scale with it, and costs and energies are scaled back to the site. When days are filled from the profile,
  the load of a missing day is taken from the same weekday and quarter-hour of the nearest day of the year in the
  data; public holidays are not treated separately.
- Weather, day-ahead prices and renewable forecasts are those of the reference site. Before every simulated month the
  Gaussian-process forecast is retrained on the weather before that month and its σ calibrated on its own held-out
  tail (walk-forward: `experiments/winter/train_rewind.py`, `app/own/prep_bundle.py`). These monthly forecast
  bundles do not depend on the load and are cached in `out/own/bundles/` (1–3 min per month).
- **Quick mode:** DET, DET-BO with the reference back-off, DET-BO with a back-off re-sized every month from the r_E of
  the month before (b = r_E (x_max − x_min) / 0.75), and UA-MPC, each with the reference weights tuned for that
  configuration, plus grid-only as the baseline. The SoC carries over between months. About 15–60 min per
  controller and month on one core (UA-MPC is the slowest); controllers run in parallel on the available cores.
- **Screening:** only r_E and Γ per month and configuration, in the time of the forecast training.
- **Reference check:** with the reference data set, the reference sizes, initial SoC 0.5, days outside the data left
  out, and months from October 2025 (stored: October 2025 – March 2026 for B and C) or the September 2026 test
  period (A, B, C), the report compares each run with the stored result within the tolerances of the re-run tab
  (net cost 0.1 %, protection 0.5 h). Any other input runs the same way without this comparison.

The report gives the cost, the saving against grid-only, protection hours, extra grid cost, curtailment and band
violation per controller, the months, r_E and Γ, the back-off that Γ = 0.75 would need, the reference check where it
applies, and downloads (`report.md`, `report.json`, `monthly.csv`). The weights are the reference weights, not
re-tuned for the site; costs are energy at Hungarian day-ahead prices plus battery wear, without network charges or
taxes. With Docker, mount `out/` so that runs and forecast bundles persist, and `models_summer/` for the September
2026 test period:

```bash
docker run --rm -p 8501:8501 -v "$PWD/out:/pkg/out" -v "$PWD/models_summer:/pkg/models_summer" ua-mpc-repro
```

An interrupted or stopped run can be resumed from the tab; finished months are kept. `OWN_PARALLEL=2` lets two runs
of the queue run at the same time (default 1).

## Citation

See `CITATION.cff`. Version 1.1.0 adds the analyses listed in `CHANGELOG.md`, 1.2.3 the reproduction of the paper and the simulation of any
load profile.

## Licence

Code: MIT (`LICENSE`). Results and the anonymised load profile: CC BY 4.0. Price and weather data: their providers'
terms (`LICENSE-DATA.md`).
