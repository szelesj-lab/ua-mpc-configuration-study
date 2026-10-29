# Data

| Source | File | Coverage | Resolution | Gaps and treatment |
|---|---|---|---|---|
| Plant main meter (meat processing, Hungary) | `code/data/industrial_load_profile.csv` | 2025-10-01 – 2026-09-30 | 1 h (held constant in 15-min steps) | 41 zero values, 1 empty, 24 hours absent and the DST hours treated as missing; 67 h (0.77 %) filled with the mean of the same weekday and hour ±1 and ±2 weeks; column `filled` marks them |
| Day-ahead price, bidding zone HU | `code/data/ENTSOE_HU_Dataset_2025.csv` | 2025-09-30 – 2026-10-22 | 15 min | none |
| Weather forecast as issued (Open-Meteo historical forecast) | `code/data/Past_Forecast_Values.csv` | 2025-06-01 – 2026-10-21 | 1 h | none |
| Weather reanalysis (Open-Meteo archive) | `code/data/Past_Real_Values.csv` | 2025-06-01 – 2026-10-16 | 1 h | none |

Site 48.088 °N, 19.910 °E; PV plane tilt 35°, south. The realised PV and wind power of the cell are computed from
the reanalysis with the physical models in `code/src/referencemodel_functions.py`; the forecast from the issued
forecasts and the GP residual models.

The plant load (about 17 GWh per year, mean about 1.9 MW; rounded) is published only as per-unit values. The profile
was produced with `code/src/prepare_load_profile.py` from the company's main-meter export, which is not public.
Sub-meter, production, gas and plant-state data of the company were inventoried (`results/analysis/inventory.csv`)
and not used. `experiments/analysis/inventory.py` needs the raw export (`COMPANY_RAW`) and is included for
transparency.

Second load case: `code/data/production_weekly.csv`, a synthetic weekly production schedule of the laboratory line.
