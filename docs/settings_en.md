# Settings of the simulation

What every setting of the **Simulation** tab means, what can be set and what it changes (version 1.3.2).
Magyar változat: [`settings.md`](settings.md). User guide: [`user_guide_en.md`](user_guide_en.md).

The simulation scales your system onto the reference cell (mean load 10.788 W), runs the controllers there and
scales the results back to your units. The scaling is one factor: 10.788 W divided by the mean of the uploaded load.
Battery, power limit, PV and wind scale with the same factor, so **only the ratios matter**: twice the load with twice
the battery gives the same result at twice the cost.

## 1. Load data

| Setting | Meaning | Effect |
|---|---|---|
| **Choose File** | The load time series as CSV: timestamp and load, 15-, 30- or 60-min steps, at least one week. | The load of the simulation. Its mean sets the scaling factor and the default sizes. |
| **Values are** | What the second column holds: average power in kW or W, or energy per interval in kWh. | Only the conversion. A wrong choice gives the wrong order of magnitude; the mean and peak shown after the check reveal it. |
| **Timestamps (without offset) in** | The time zone of timestamps written without a UTC offset. | Which hour the load falls on relative to the weather and the price. No effect on timestamps with an offset (`+01:00`). |
| **Check file** | Reads and checks the file. | Stops on errors; shows warnings (zeros, spikes, gaps) and lets you continue. Gaps up to 2 h are interpolated. |
| **Use the reference data set** | The measured plant load shipped with the package (the paper's data) with the paper's system sizes. | A reference run: the report compares the result with the stored one (Reference check). The same happens when `reference_load_profile_2025-26.csv` is uploaded unchanged. |

## 2. System

The defaults are the reference system's ratios to the mean of the uploaded load (**Reference ratios** restores them).

| Setting | Meaning | Default | Effect |
|---|---|---|---|
| **Battery capacity (kWh)** | The battery's total energy capacity; 80 % of it (the 10–90 % SoC band) is usable. | 6.67 h of the mean load | A larger battery shifts more energy, saves more and reaches the band edge less often (fewer protection hours). r_E is inversely proportional to it, so with a large battery uncertainty handling matters less. |
| **Battery power limit (kW)** | The maximum charge and discharge power (the same for both). | 2.22 × the mean load | A lower limit charges and discharges more slowly and saves less. In B it also caps the forecast error that reaches the battery (r_E). |
| **PV (kWp)** | PV peak power. Generation comes from the reference site's weather (48.09 °N, 35° tilt, south), scaled in proportion to the reference PV. | 2.78 × the mean load | More PV: less energy bought, more surplus to shift or curtail. The forecast error grows in proportion, and so does r_E. |
| **Wind turbine, rated (kW)** | Rated power of the wind turbine (cut-in 1 m/s, rated 8 m/s, cut-out 12 m/s). | 1.86 × the mean load | As for PV: more wind means less energy bought and a proportionally larger forecast error (r_E). 0 means no wind. |

### Configurations

Several can be selected in one run; each runs separately with the same load and sizes.

| Configuration | How it works | Where the forecast error goes | What to expect |
|---|---|---|---|
| **A – renewable buffer** | Renewables only charge the battery; battery and grid supply the load. Renewable energy not taken is lost. | A shortfall reduces the planned charge, i.e. reaches the battery. | More renewable energy is curtailed, because it reaches the load only through the battery; without a battery (grid only) all of it. |
| **B – self-consumption** | Renewables supply the load directly; the battery takes surplus and deficit. | Both directions reach the battery. | Uncertainty handling (DET-BO, UA-MPC) matters most here: the controllers differed here in the paper. |
| **C – set-point** | The battery follows its planned power; the grid balances the rest. | The grid; nothing reaches the battery (r_E = 0). | The controllers are practically the same; uncertainty handling brings nothing. Only DET and UA-MPC run, no DET-BO. |

### Controllers

All re-plan every hour over a 24-h horizon with the weights tuned for the reference cell. **Grid only** (no battery)
always runs; the saving is computed against it.

| Controller | How it handles uncertainty | What to expect |
|---|---|---|
| **DET** | Not at all: it plans with the forecast mean only. | When the forecast errs, the SoC reaches the band edge more easily. The version tuned for B counters this with a strong pull of the SoC to the middle, which means less arbitrage and a higher cost. |
| **DET-BO (reference back-off)** | A constant reserve b at both edges of the SoC band, as tuned in the reference study: b = 0.045 in A, 0.030 in B. | If your r_E is larger than b was sized for (Γ > 1), more protection hours; if smaller, it is needlessly cautious. |
| **DET-BO re-sized from r_E** | The same, but b is set at the start of every month from the r_E of the month before: b = r_E · 0.8 / 0.75 (aiming at Γ = 0.75). The first month uses the r_E of the period before it. | Follows seasonal change; in the paper's winter test it needed fewer protection hours than UA-MPC at about 0.7 % higher cost. |
| **DET-BO scenario back-off** (unchecked by default) | A constant reserve chosen at the start of every month from the forecast archive of the month before: the largest 1-h deficit that reached the battery (the scenario method with one decision variable). | Almost always enough (Γ = 0.15–0.62 in the paper) but locks 14–35 % of the usable band, so it costs more. Test T3 of the paper. |
| **DET-BO robust back-off** (unchecked by default) | The same for the worst case: the largest 15-min deficit of the month before, held for a whole hour (tube tightening). | The most cautious (Γ = 0.09–0.59); locks 23–53 % of the band. Test T3 of the paper. |
| **UA-MPC** | Sets the reserve at every step from the calibrated forecast spread (chance constraint); the spread is re-calibrated every month. | Needs no sizing; in the paper it was 2.9–3.3 % cheaper than DET in B. If forecast errors have heavier tails than the Gaussian assumption (e.g. in winter), its reserve may be too small. |

## 3. Period and mode

| Setting | Meaning | Effect |
|---|---|---|
| **quick** | The controllers run. | Full results: cost, protection hours, figures, every step and every MPC solve. About 15–60 min per controller and month for one configuration. |
| **screening** | No controller runs; only r_E and Γ per month and configuration. | Tells within minutes whether uncertainty handling is worth considering (hardly with r_E ≈ 0 or Γ < 0.75). |
| **months, From / To** | The simulated calendar months between October 2025 and September 2026; run one after another, the SoC carries over. | Before every month the forecast model is retrained on the weather until then (once per month, 1–3 min; stored afterwards). More months: a proportionally longer run. |
| **test period Sep 2026** | 2–29 September 2026 with the summer forecast models (the paper's final test). | Needs the `models_summer` folder. With the reference data it is compared with the stored results of the paper's final test (A, B, C). |
| **Days the load data do not cover: fill from the profile** | If the file does not cover a simulated day, the load is taken from the same weekday and quarter-hour of the nearest day of the year in the data. | A short file (even one week) can drive a whole year; seasonal change is only as present as in the file. Public holidays are not treated separately. |
| **leave out** | Cuts the simulated period to the first and last day of the data. | Only real data are simulated. If the file covers none of the chosen months, the run stops with an error. |
| **Γ target of the re-sized back-off** | The Γ the re-sized DET-BO aims at: b = r_E of the month before × 0.8 / Γ target. Default 0.75 (rule T1 of the paper). | Lower: a larger reserve, fewer BMS interventions, higher cost; higher: the reverse. Above 1 the reserve does not cover the deficit of the bad hours. |
| **Initial SoC** | State of charge at the start of the first month (0.1–0.9). | Affects the cost of the first days; negligible in longer runs. 0.5 is needed for the reference comparison. |
| **Name** | The run's name in the list. | Display only. |
| **Add to queue** | Puts the run at the end of the queue. | Starts at once if nothing runs, otherwise waits for the runs before it. |

## 4. Fixed settings (not adjustable in the interface)

| Setting | Value | Note |
|---|---|---|
| SoC band | 10–90 % | The BMS intervenes at its edges (protection hours). |
| Battery efficiency | 0.95 on charge and on discharge | |
| Battery wear | 20 €/MWh of energy throughput (charge + discharge) | Part of the net cost. |
| Time step, horizon, re-planning | 15 min, 24 h (96 steps), hourly | |
| Tariff | Hungarian day-ahead price (ENTSO-E), import only | No export revenue, network charges, taxes or demand charge; surplus that cannot be used is curtailed. |
| Weather, forecasts | those of the reference site, 2025–2026 | Only the load and the sizes are yours. |
| Controller weights | tuned for the reference cell | Not re-tuned for your system; tuning could change the ranking. |
| Parallel runs | 1 (environment variable `OWN_PARALLEL`) | Within a run the controllers run in parallel on the cores. |

## 5. Quick tips

- **Start with screening** for the months of interest: with r_E ≈ 0 (e.g. in C) the controllers will not differ.
- **One month, one configuration** for the first quick run; extend when it looks right.
- **To compare sizes**, press **Use these settings** on a finished run, change the battery and queue it.
- **To reproduce the paper's results**, see section 5 of the user guide: reference data, leave out, initial SoC 0.5.
