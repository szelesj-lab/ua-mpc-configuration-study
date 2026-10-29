#!/bin/bash
# Walk-forward winter test (October 2025 .. March 2026): per month, train the GP on the data before the month
# (train_rewind.py), calibrate sigma on its held-out tail and build the month's replay data (prep_month.py),
# then replay the pre-registered summer controllers of B and C month by month (run_rewind.py).
# The controllers are those of $SUMMER_RESULTS (default: the shipped results/summer). Resumable.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
H=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$H/../.." && pwd)
export WORK=${WORK:-$PKG/work}
OUT=${OUT:-$PKG/out}
export WINTER_RESULTS=${WINTER_RESULTS:-$OUT/winter}
mkdir -p $WINTER_RESULTS/logs
LOG=$WINTER_RESULTS/logs/pipeline.log
ENDS=(2025-11-01 2025-12-01 2026-01-01 2026-02-01 2026-03-01 2026-04-01)
for i in 1 2 3 4 5 6; do
  R=$WORK/W/m$i; C=$(cat $R/CUTOFF)
  if [ ! -f $R/models/gpr_wind_model.joblib ]; then
    echo "$(date +%H:%M) START train m$i ($C)" >> $LOG
    (cd $R && OMP_NUM_THREADS=2 python3 $H/train_rewind.py $R $C > $WINTER_RESULTS/logs/train_m$i.log 2>&1) \
      && echo "$(date +%H:%M) END train m$i" >> $LOG || { echo "$(date +%H:%M) FAILED train m$i" >> $LOG; exit 1; }
  fi
  if [ ! -f $R/results/rewind/calibration.json ]; then
    echo "$(date +%H:%M) START prep m$i" >> $LOG
    (cd $R && python3 $H/prep_month.py $R $C ${ENDS[$((i-1))]} > $WINTER_RESULTS/logs/prep_m$i.log 2>&1) \
      && echo "$(date +%H:%M) END prep m$i" >> $LOG || { echo "$(date +%H:%M) FAILED prep m$i" >> $LOG; exit 1; }
  fi
  mkdir -p $WINTER_RESULTS/calibration
  cp $R/results/rewind/calibration.json $WINTER_RESULTS/calibration/m$i.json; cp $R/CUTOFF $WINTER_RESULTS/calibration/m$i.cutoff
done
echo "$(date +%H:%M) START replay" >> $LOG
python3 $H/run_rewind.py >> $WINTER_RESULTS/logs/run.log 2>&1 || { echo "$(date +%H:%M) FAILED replay" >> $LOG; exit 1; }
python3 $H/run_rewind.py stats > $WINTER_RESULTS/logs/stats.log 2>&1 && echo "$(date +%H:%M) WINTER DONE" >> $LOG
