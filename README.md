# ua-mpc-configuration-study

Simulation package of

> J. Szeles, L. Deszát, A. Kummer, A. Fodor, T. Ruppert: *Configuration-dependent value of uncertainty-aware MPC
> for behind-the-meter batteries in food processing* (manuscript submitted to Applied Energy).
> University of Pannonia, Hungary.

It holds the controller code in the exact version used, the experiment pipelines, every result file, and the
scripts that produce all tables, numbers and figures of the paper.

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23773120.svg)](https://doi.org/10.5281/zenodo.23773120)

## The study in short

A laboratory-scale production cell (30 W PV, 20 W wind turbine, 72 Wh battery) is driven by one year of measured
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
| `results/analysis/` | data inventory, period and monthly energies, protection hours by SoC edge, decision-map table; `revision/`: solver convergence, quantile sensitivity of r_E, calibration per channel, stricter-solver re-run of B UA-MPC (`experiments/analysis/solver_sensitivity.py`) |
| `results/production_schedule/` | second load case |
| `docs/` | protocol and data description |

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
`code/src/scicomp_training.py` on the weather data before 16 June 2026 and are archived with the Zenodo record of
this package (`models_summer.zip`); unpack them to `models_summer/` before `make_work.sh` and check them against `docs/models_summer.sha256`. GP fitting uses
random restarts, so models re-trained from scratch give slightly different forecasts.

### Notes

- The ENTSO-E API key is only needed to update the price data and is read from `.env` (never stored in code).
- Every pipeline step is resumable and leaves a marker; an interrupted run continues where it stopped.
- Small numerical differences between machines (BLAS threads) can shift single protection events; the published
  runs used one BLAS thread per process (`OMP_NUM_THREADS=1`).

## Data

See `docs/data.md` and `LICENSE-DATA.md`. The plant load is anonymised (per unit, no absolute values, no meter or
company names); the raw company export is not included.

## Citation

See `CITATION.cff`. Version 1.0.0 is archived at Zenodo: https://doi.org/10.5281/zenodo.23773120

## Licence

Code: MIT (`LICENSE`). Results and the anonymised load profile: CC BY 4.0. Price and weather data: their providers'
terms (`LICENSE-DATA.md`).
