# Replication with south-facing PV

The weather data of the first study were downloaded with Open-Meteo azimuth 180, which is a north-facing panel,
while the manuscript describes a south-facing one (configuration error in `code/config/dataConfig.json`).
The whole protocol is being repeated with azimuth 0 (south) on the same site, periods, prices and load.

- `data_south/`: the weather data of the replication (hourly, local time), downloaded on 8 November 2026:
  historical forecast and reanalysis with tilt 35°, azimuth 0, and the forecast of the previous day's model run
  (`Past_Forecast_Values_day1.csv`, fixed lead time) for the look-ahead check.
- `preregistration_R0.json` (written by `experiments/redo/prereg_r0.py`): before any replay of the replication,
  the data checksums, the calibration (κ = 1.051, φ = 0.822), r_E of every period and decision-map cell and the
  predictions that follow from the rules of the manuscript without the tuned weights. Record R1 (with the tuned
  back-off) follows after the selection and before the decision map and the final test.
