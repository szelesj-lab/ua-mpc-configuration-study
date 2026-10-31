# Revision analyses (v1.1.0)

Analyses added in response to the reviews of the manuscript. The scripts are the versions that produced
`results/revision/`; only their absolute paths were replaced by three environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `UA_WORK` | `work/revision` | working tree the scripts read and write (built by `link_tree.sh`) |
| `UA_CODE` | `code` | controller code (Kairos) |
| `UA_KAIROS` | `work` | work copies made by `experiments/make_work.sh` (`work/A`, `work/B`, `work/C`, `work/W/m1..m6`) |

```bash
experiments/make_work.sh                 # work copies (needs the summer GP models, see the main README)
experiments/revision/link_tree.sh        # builds $UA_WORK from the package with symbolic links
export UA_WORK=$PWD/work/revision UA_CODE=$PWD/code UA_KAIROS=$PWD/work
```

Scripts that only read result files (the indicator benchmark, `summarize.py`) run in seconds. Scripts that replay
controllers need the work copies, the summer GP models and, for the winter months, the walk-forward data of
`experiments/winter/run_winter.sh`. Run times below are on 2 cores.

## What each analysis gives in the paper

| Folder | Script(s) | Results (`results/revision/`) | Paper | Time |
|---|---|---|---|---|
| `sensitivity/` | `rev2_runs.py`, `run_all.sh`, `run_part.sh` (env `CONFIGS`, `SCEN_LIST`), `summarize.py` | `sensitivity/`: stricter solver, 10 % load-forecast error, ±10 % renewable bias, channel κ; `sens.csv`, `tab_sens.tex` | Sec. 5.6, Table B (sensitivity) | ~8 h |
| `calibration/` | `calib_cond.py` | `calibration/calib_conditional.csv`, `kappa_channel.json` | Sec. 5.1, Table 5 | minutes |
| `calibration/` | `calib_diag.py` | `calibration/calib_diag.csv`: PIT deciles and the standardised window-error quantiles | Sec. 5.5, tail-diagnostics table (App. B) | minutes |
| `ratio/` | `rE_validate.py`, `rE_discrim.py`, `rE_periods.py` | `ratio/`: decision-map cells and labels, AUC, leave-one-out, bounds out of sample | Sec. 5.3, Table (out of sample) | minutes |
| `ratio/` | `effective_gamma.py` | `ratio/effective_gamma.csv`: Γ of the UA-MPC mean margin and of the tuned back-off per period | Sec. 4.4 (relation to the margin), Sec. 5.5 | minutes |
| `preregistered/` | `prereg.py` (writes `preregistration_rE.json` before the runs), `run_rE.py` (`grid`, `correlation`, `b_sweep`), `chain.sh`, `common.py`, `figures_rev3.py` | `preregistered/preregistration_rE.json`, `runs_rE/`, `figs/` | Sec. 4.8, 5.4, Fig. Γ, summary figure | ~12 h |
| `preregistered/` | `prereg2.py` (writes `preregistration_T1T2.json` before the runs), `run_t1.py`, `run_t2.py` | `preregistered/preregistration_T1T2.json`, `t1/`, `t2/`, `RESULTS.md` | T1 and T2 (Sec. 4.8, 5.4, Tables T1/T2) | ~3 h |
| `tariffs/` | `expost.py` (re-pricing), `run_glim.py` (import limit in the MPC) | `tariffs/expost.csv`, `glim_B/`, `glim_C/` | Sec. 5.6, tariff table (App. B) | ~2 h |
| `seasonal/` | `run_season.py select <ctrl>`, `run_season.py test` | `seasonal/reselection.json`, `select/`, `test/` (`REUSED.txt`: unchanged chains copied from the winter test) | Sec. 5.5 (seasonal re-selection) | ~9 h |
| `indicators/` | `lit_indicators.py industrial|second`, `lit_indicators_corr.py`, `grouped_cv.py` | `indicators/`: r_E against storage-sizing indicators, AUC, leave-one-out, leave-one-family-out | Sec. 5.3, indicator table (App. B) | minutes |
| `figures/` | `graphical_abstract.py`, `fig_architecture.py`, `fig01_coverage.py`, `fig15_split.py`, `fig16_col.py` | written to the manuscript's `figs/` | graphical abstract, Figs. 1 and 5, the summary figures (cost and protection) and the column-width Γ figure; the last two read the data files written by `preregistered/figures_rev3.py` | seconds |

The time stamps of the two preregistration records (`written_utc`) precede the first run of the tests they
cover; the run logs are next to the results.
