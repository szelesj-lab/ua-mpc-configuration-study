# User guide

This guide covers the web interface and command line of the package (version 1.2.1). The tool does two things:

1. **Checks the published results.** It regenerates every number, table and figure of the article from the stored
   result files, compares them with the article, and re-runs single stored experiments.
2. **Simulates any load profile.** It runs the controllers (DET, DET-BO, UA-MPC) on a load profile you upload, with
   a battery, PV and wind sized by you. Run with the reference data set and the reference settings, it reproduces the
   stored results and shows the comparison.

Contents: [Starting](#1-starting) · [The interface](#2-the-interface) · [Simulating a load profile](#3-simulating-a-load-profile)
· [Reading the results](#4-reading-the-results) · [Reproducing the reference results](#5-reproducing-the-reference-results)
· [Command line](#6-command-line) · [Files and folders](#7-files-and-folders) · [Troubleshooting](#8-troubleshooting)
· [Limits](#9-limits)

## 1. Starting

### With Docker (Windows, macOS, Linux)

Windows needs Docker Desktop, which needs WSL 2 (`wsl --install` in an administrator PowerShell, then a restart).

```bash
docker build -t ua-mpc-repro .
docker run --rm -p 8501:8501 -v "$PWD/out:/pkg/out" ua-mpc-repro
```

Open http://localhost:8501. The `-v "$PWD/out:/pkg/out"` part keeps your runs and the trained forecasts between
starts; without it they are lost when the container stops.

For re-running stored experiments and for the September 2026 test period, also mount the summer forecast models
(`models_summer.zip`, attached to the GitHub release v1.2.1):

```bash
unzip models_summer.zip -d models_summer
docker run --rm -p 8501:8501 -v "$PWD/out:/pkg/out" -v "$PWD/models_summer:/pkg/models_summer" ua-mpc-repro
```

### Without Docker (Linux, macOS, WSL)

Python 3.13, from the package root:

```bash
pip install -r requirements-frozen.txt -r code/requirements.txt
python -m app.server            # http://localhost:8501  (--port, --host 0.0.0.0 to change)
```

Stop the server with Ctrl+C. Runs started from the interface keep running in the background until they finish.

## 2. The interface

The header shows the state of the check against the article: values of the text, table cells and figures that
match. **Regenerate and check** rebuilds everything from the result files (about a minute); **Download report**
saves the report of the last check.

| Tab | What it shows |
|---|---|
| **Values** | Every number quoted in the text: as printed, as recomputed, ✓ or ✗, and the file or script it comes from. Filter with the search box or "only values that differ". |
| **Tables** | Every numeric cell of every table; differing cells are highlighted with both values. |
| **Figures** | Each figure as printed next to the regenerated one, with the share of differing pixels (≤ 1 % counts as identical). Click to enlarge. |
| **Re-run experiments** | Re-runs a stored experiment (forecast calibration, a run of the September test period or of the decision map) and compares it with the stored result. Needs the summer models. |
| **Your data** | Simulation of any load profile (sections 3–5). |
| **History** | Earlier reports and the log of every step. |

![Values tab](img/guide/01_values.png)

![Figures tab](img/guide/02_figures.png)

**Re-run experiments.** Pick an experiment on the left, press **Re-run**. The panel shows the MPC solves done, the
SoC of the run so far and, at the end, the comparison with the stored result (net cost within 0.1 %, protection
within 0.5 h, band violation within 0.05 pp·h, extra grid cost within 0.0005 €, curtailment within 0.2 %). The
first re-run prepares working copies of the code (about 1.2 GB).

![Re-run experiments](img/guide/06_rerun.png)

## 3. Simulating a load profile

Tab **Your data**. The left column is the form, the right column the selected run and the list of runs.

![The form](img/guide/03_form_empty.png)

### 3.1 Sample CSV

Download an example file in the accepted format: choose its length (week, month, year), resolution, unit and mean
load, and press **Download sample CSV**. It is ready to upload, or to overwrite with your own values.

### 3.2 Load data

The file needs two columns: a timestamp and the load. A header row, `#` comment lines and `;` or `,` separators
are accepted; decimal commas are accepted when the separator is `;`.

```csv
timestamp,load_kw
2026-03-02 00:00+01:00,98.512
2026-03-02 01:00+01:00,96.730
```

| Item | Accepted |
|---|---|
| Resolution | 15, 30 or 60 min (constant within the file) |
| Value | average power in kW or W, or energy per interval in kWh (choose under "Values are") |
| Timestamp | start of the interval; with a UTC offset (as in the sample) or without one, in the zone chosen under "Timestamps … in"; daylight-saving changes are handled |
| Length | at least one full week (every weekday and quarter-hour present) |
| Gaps | up to 2 h are interpolated; longer ones are reported |

Press **Check file**. The result lists the rows, the period, mean and peak load, gaps, and warnings (zeros above 5 %,
spikes above 20 × the median, negative values, duplicates); errors stop the upload. Two plots show the daily mean
load and the typical week.

Or press **Use the reference data set**: the measured plant load shipped with the package
(`code/data/industrial_load_profile.csv`, October 2025 – September 2026; see `docs/data.md`), with the reference
system's sizes and settings filled in (section 5).

### 3.3 System

Battery capacity (kWh), battery power limit (kW, charge and discharge), PV peak power (kWp) and wind turbine rated
power (kW), in your site's units. They are filled with the reference system's ratios to your mean load
(battery 6.67 h of mean load, power 2.22 ×, PV 2.78 ×, wind 1.86 × the mean load); **Reference ratios** restores
them. PV or wind can be set to 0.

**Configurations** — how the battery is connected:

| | Configuration | Where a renewable forecast error goes |
|---|---|---|
| A | renewable buffer: renewables only charge the battery; battery and grid supply the load | a shortfall reduces the planned charge, i.e. reaches the battery |
| B | self-consumption: renewables supply the load, the battery takes surplus and deficit | both directions reach the battery |
| C | set-point: the battery follows its planned power, the grid balances the rest | the grid; nothing reaches the battery (r_E = 0) |

**Controllers**:

| | Controller |
|---|---|
| DET | deterministic MPC on the forecast mean |
| DET-BO (reference back-off) | DET with a constant SoC reserve b at both edges of the band, as tuned in the reference study (A and B) |
| DET-BO re-sized from r_E | DET-BO whose b is set each month from the r_E of the month before: b = r_E (x_max − x_min) / 0.75 (A and B) |
| UA-MPC | uncertainty-aware MPC: the reserve follows the calibrated forecast uncertainty |

Grid only (no battery) always runs as the baseline. All controllers use the weights tuned for the reference cell.

### 3.4 Period and mode

- **Mode.** *Quick* runs the controllers. *Screening* computes only r_E and Γ per month and configuration (no
  controller runs; takes as long as the forecast training).
- **Period.** A range of months between October 2025 and September 2026, or the *test period Sep 2026* (2–29
  September 2026, with the summer forecast models; needs `models_summer`). Months are simulated one after the other
  and the SoC carries over.
- **Days the load data do not cover.** *Fill from the profile*: such a day takes the load of the same weekday and
  quarter-hour from the nearest day of the year in your data (so a one-month file can drive a whole year). *Leave
  out*: the simulated period is cut to the first and last day of your data.
- **Initial SoC** at the start of the first month (0.1–0.9), and an optional **name** for the run.

The line above the button estimates the duration. Press **Run simulation**.

![Form with the reference data set](img/guide/04_form_reference.png)

### 3.5 While it runs

The right panel shows the stage (forecast training, simulation, report), one progress bar per controller with its
SoC trace, and the remaining time at the current speed. You can close the page; the run continues and appears in
the list of runs.

Durations: the forecast of each month is trained once (1–3 min) and kept for later runs. A controller takes about
15–60 min per month on one core (UA-MPC is the slowest); controllers run in parallel on the available cores. If the
machine or the container stops, the run is marked *failed*; **Resume** continues it and keeps the finished months.

## 4. Reading the results

![Result of a run](img/guide/05_result.png)

**Verdict** (blue box, per configuration): r_E of the period, Γ with the reference back-off and what it suggests,
and the constant back-off that would give Γ = 0.75.

- **r_E** — generalised energy-deficit ratio: in a bad hour (95 % of the hours are better), the share of the usable
  battery capacity that the forecast error removes. Only the error that the configuration routes to the battery
  counts, so r_E = 0 in set-point operation.
- **Γ** ("capital gamma") — reserve utilisation: the bad-hour deficit divided by the constant reserve b,
  Γ = r_E (x_max − x_min) / b. Below 1 the constant reserve is enough and uncertainty handling adds little; above 1
  it is not. In the reference tests the switch lay between 0.75 and 1.25. Colours in the month table: green < 0.75,
  amber 0.75–1.25, red > 1.25.

**Bars and summary table** (per configuration, whole period, in your units):

| Column | Meaning |
|---|---|
| Net cost (€) | energy bought at the day-ahead price plus battery wear, minus the value of the energy left in the battery at the end |
| Saving vs grid | against grid only (no battery), in € and % |
| vs DET | net cost relative to DET |
| Protection (h) | hours in which the battery management system had to cut charge or discharge because the SoC reached the band edge |
| Extra grid (€) | cost of the energy the battery could not deliver because of these cuts |
| Curtailed (%) | share of the renewable energy that could not be used |
| Band violation (pp·h) | depth × duration of SoC excursions outside the planned band |

**Month table**: r_E, r_E of the month before, Γ, the re-sized and the recommended back-off, and net cost /
protection hours of each controller per month.

**Reference check** (only with the reference data set; section 5): each run against the stored result, ✓ when
within the tolerances.

**Downloads**: `report.md` (the full report), `monthly.csv` (one row per configuration, controller and month,
with all metrics), `report.json` (everything, machine-readable).

## 5. Reproducing the reference results

The simulation reproduces the stored results when it gets the same inputs:

1. **Use the reference data set** (sizes 0.072 kWh, 0.024 kW, PV 0.03 kWp, wind 0.0200704 kW are set; these are
   the reference cell's 72 Wh, 24 W, 30 W and 20.07 W).
2. Keep **leave out** for days outside the data and initial SoC **0.5**.
3. Choose the configurations and either months starting in **2025-10** (stored: October 2025 – March 2026 for B and
   C) or the **test period Sep 2026** (A, B and C).
4. Run in quick mode.

The report then contains a **Reference check** table with each run against the stored one. Checked on
2026-11-04: B and A in the September 2026 test period, C in October 2025 and B in November 2025 all matched (most
runs bit for bit; DET-BO and UA-MPC of B in September within 5·10⁻⁵ of the net cost, protection hours equal). If
anything differs from these settings, the run is an ordinary simulation and the report says why it is not compared.

## 6. Command line

```bash
python -m app.reproduce        # check against the article; report in out/report.md, exit code 0 if all match
docker run --rm ua-mpc-repro python -m app.reproduce
python -m pytest -q app/tests  # unit tests of the upload, mapping and reference check (pip install pytest)
```

## 7. Files and folders

| Path | Content |
|---|---|
| `out/report.md`, `out/report.json`, `out/reports/` | check against the article, latest and earlier |
| `out/figures/`, `out/manuscript/` | regenerated figures and numbers |
| `out/rerun/<id>/` | re-runs of stored experiments |
| `out/own/uploads/<id>/` | checked uploads |
| `out/own/jobs/<id>/` | simulations: `params.json`, `report.md`, `report.json`, `monthly.csv`, `runs/` (every step as CSV) |
| `out/own/bundles/<month>/` | trained monthly forecasts (reused by later runs; delete to retrain) |

## 8. Troubleshooting

| Message or symptom | Cause and fix |
|---|---|
| `docker` is not recognised (PowerShell) | Docker Desktop is not installed or not on the PATH; install it and open a new PowerShell. |
| "failed to connect to the docker API" | Docker Desktop is not running; start it and wait until it reports "running". |
| Docker Desktop: WSL not installed | `wsl --install` in an administrator PowerShell, restart, start Docker Desktop. |
| "models missing" (Re-run, test period Sep 2026) | unpack `models_summer.zip` into `models_summer/` and mount it (section 1). |
| "the resolution must be 15, 30 or 60 minutes" | the timestamps are irregular or another step; resample the file. |
| "at least one full week is needed" | the file is shorter than 7 days or some weekday and hour are missing. |
| many negative values | export or a sign convention; the load must be consumption ≥ 0. |
| "the load data cover none of the chosen months" | with *leave out*, choose months the file covers, or use *fill from the profile*. |
| run marked *failed* | see its log (in the panel); **Resume** continues from the last finished month. |
| very slow | each controller and month takes 15–60 min per core; choose fewer controllers or months, or a machine with more cores. |

## 9. Limits

- Weather, day-ahead prices and renewable forecasts are those of the reference site (Hungary, 48.088 °N, 19.910 °E,
  2025–2026); only the load and the sizes are yours.
- Costs are energy at day-ahead prices plus battery wear, without network charges, taxes or demand charges.
- The controller weights were tuned for the reference cell and are not re-tuned for your site; the ranking of the
  controllers can change after tuning.
- The battery is the reference cell model (efficiency 0.95, SoC band 0.1–0.9, BMS protection) scaled to your size;
  ageing, inverter and BMS behaviour need not scale with it.
- In *fill from the profile*, public holidays are not treated separately.
