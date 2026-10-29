# Kairos UA-MPC – development-period results (2026-10-23)

Scenario: 72 Wh battery, c_deg = 2e-5 €/Wh, re-planning every 4 steps (1 h), margin_window = replan.
Periods (Europe/Budapest): calibration 06-16–07-07, tuning 07-07–08-11, development 08-11–09-02 (22 days),
final test 09-02–10-15 (NOT run yet).

tuning/        – ParEGO tuning of det, det_bo, ua on the tuning period (30 trials, 4x2-day episodes, equal budget)
dev_ablation/  – each controller with its tuning "balanced" (= safest) point, physical and bms plant
                 setup.json (exact overrides), summary.csv, paired_bootstrap.csv (ua − other, daily, 5-day
                 circular blocks, 10 000 draws, 95 % CI), steps/ (15-min closed-loop trajectories)
dev_fronts/    – every Pareto point of det/det_bo/ua replayed on the development period (physical plant)
                 fronts_dev.csv (joint_front = non-dominated in dev cost x dev violation),
                 paired_selected.csv (neighbouring points, totals over 22 days)
ablation.py, ablation_stats.py, front_dev.py – the scripts that produced these (run from the repo root)

Ablation variants: ua_rho_only = ua with mpc.soc_chance_z = 0; ua_margin_only = ua with weights.lambda = 0;
oracle uses the det weights.
