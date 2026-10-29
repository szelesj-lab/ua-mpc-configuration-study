# Protocol

## Periods (UTC)

| Period | From | To | Use |
|---|---|---|---|
| Calibration | 2026-06-15 22:00 | 2026-07-06 22:00 | σ scale κ and AR(1) coefficient φ of the forecast errors |
| Tuning | 2026-07-06 22:00 | 2026-08-10 22:00 | ParEGO tuning, 4 episodes × 2 days per evaluation, 30 evaluations per controller |
| Development | 2026-08-10 22:00 | 2026-09-01 22:00 | replay of every Pareto point, point selection, decision map |
| Final test | 2026-09-01 22:00 | 2026-09-29 03:45 | run once per configuration with the summer-selected points |
| Winter | 2025-09-30 22:00 | 2026-03-31 22:00 | walk-forward test, six monthly GP models |

Common scenario: battery 72 Wh, SoC band 10–90 %, charge and discharge limit 24 W, efficiency 0.95 per direction,
wear cost 2·10⁻⁵ €/Wh, re-planning every hour, horizon 24 h in 15-min steps, BMS plant (the battery stops at the
band edges, the grid covers a shortfall, a surplus is curtailed), import at the day-ahead price, no export, no
demand charge, perfect load forecast.

## Selection rule, recorded before the final-test replay

> industrial-load study, plant 'bms': per controller, among its tuning-period Pareto points replayed on the
> development period, the cheapest development net cost with battery protection ≤ threshold hours; if none,
> the point with the fewest protection hours. Primary threshold 5 h, secondary 10 h.

| Configuration | Rule and selected points recorded (UTC) | First final-test run (UTC) |
|---|---|---|
| B | 2026-10-27 03:46 | 2026-10-27 08:50 |
| C | 2026-10-28 20:40 | 2026-10-28 23:02 |
| A | 2026-10-29 04:14 | 2026-10-29 05:20 |

The records are `results/summer/<X>/final_test/preregistration.json`; every final-test start is logged in
`results/summer/<X>/final_test/final_test_runs.log`. The final-test runs were interrupted by a 2-h process limit
and resumed (each variant is a separate, resumable job; finished variants are not re-run).

## Walk-forward winter test

For month m (October 2025 … March 2026):

1. `train_rewind.py`: the unchanged GP training (`code/src/scicomp_training.py`) on the weather data before the
   first day of m only; its chronological last 20 % are held out from training.
2. `prep_month.py`: κ and φ calibrated on the held-out tail (at most 30 days before the cut-off); the month's replay
   data with the plant load and real prices; out-of-sample RMSE and coverage on the test month (reported, not fitted).
3. `run_rewind.py`: the summer-selected controllers of B (`det`, `det_bo`, `ua`, `grid_only`) and C (`det`,
   `ua`, `grid_only`) replayed month by month with the SoC carried over.

## Statistics

Paired circular block bootstrap of the daily values (5-day blocks, 10 000 resamples, full days only), 95 %
percentile interval of the total difference (`engine.paired`).

## Chronology and what "held out" means

All periods lie in the past; the study replays recorded data. The final test is held out, not prospective:
for every configuration the selection rule and the selected points were written to the record above before the
final-test period was replayed with any controller of the industrial-load study, and every final-test start is
logged. The weather and prices of that period had been replayed once before, in the final test of the second
load case (production schedule, configuration A, 23–24 October 2026). The controller weights, the plant model
(BMS plant, decided on 26 October 2026 from tuning and development results) and the selection rule were fixed on
the tuning and development periods only.
