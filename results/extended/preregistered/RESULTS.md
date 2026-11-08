# T1/T2 outcomes (evaluated 2026-10-31 against preregistration_T1T2.json, written 2026-10-30T13:11:15Z)
T1 winter Nov-Mar (B): re-sized DET-BO 4.8148 EUR / 7.5 h; tuned DET-BO 4.7861 / 32.25; UA 4.7821 / 39.5; DET 4.9135 / 23.25
P6 PASS (7.5 <= 32.25), P7 PASS (7.5 <= 39.5), P8 PASS (+0.68 % vs UA, limit 2 %)
P9 summer final: sized 0.7831 / 10.0 h vs UA 0.7825 / 9.5 h -> tie (map-cell rule) PASS, +0.08 %
T2 second case: P10 18/20 (two misses: cap72 err1.0 rp16 at Gamma 1.5 and 2: viol 0.346 / 0.866 vs UA 0.036 -> >2x but < +1 pp h absolute floor -> tie, predicted difference)
P11 4/4 (cost rises monotonically with b in every cell)
Gamma=1 cost vs UA: +0.02 %, +0.28 %, +0.32 %, +0.14 % (all ties)
Seasonal re-selection (Nov m2): det t17, det_bo t16 unchanged; ua t20 -> t13. Dec-Mar UA 3.8739 / 37.75 h (orig 3.8752 / 33.5 h)

# T3 outcomes (evaluated 2026-11-08 against preregistration_T3.json, written 2026-11-08T13:18:18Z)
T3 winter Nov-Mar (B): scenario back-off 4.8599 EUR / 2.75 h; robust back-off 4.8751 / 3.00 h; re-sized (T1) 4.8148 / 7.50; UA 4.7821 / 39.50
P12 PASS (2.75 <= 7.50), P13 PASS (3.00 <= 7.50), P14 PASS (4.8751 >= 4.8599 >= 4.8148), P15 PASS (+1.63 %, +1.94 % vs UA)
P16 summer final: scenario 0.7918 / 8.25 h, robust 0.8078 / 3.50 h vs UA 0.7825 / 9.50 h -> both tie (map-cell rule), +1.11 % / +3.16 % vs re-sized 0.7831 -> PASS
