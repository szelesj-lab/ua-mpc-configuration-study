#!/bin/bash
# Second load case of the replication: configuration A (renewable buffer) with the weekly production schedule as the
# load and the physical plant, as in the first study's second load case (72 Wh, c_deg 2e-5, 1-h re-planning).
# Equal-budget ParEGO tuning (30 trials, 4 x 2-day episodes; objectives net cost, violation, curtailment) ->
# development fronts -> selection by violation (stages_second.py prereg). With SELECT_ONLY=1 it stops there, so
# that record R2 (prereg_r2.py) is written before the decision map, the final test and T2.
# Resumable: markers in $OUT/second/done.
#   WORK=<work> OUT=<out> [SELECT_ONLY=1] bash experiments/redo/run_second_redo.sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
R=$(cd "$(dirname "$0")" && pwd); N=$(cd "$R/../summer" && pwd); PKG=$(cd "$R/../.." && pwd)
WORK=${WORK:-$PKG/work}; OUT=${OUT:-$PKG/out}
P=$WORK/P; LOG=$OUT/second/pipeline.log
mkdir -p $OUT/second/done $OUT/second/logs
step() {  # step <marker> <command...>
  local m=$OUT/second/done/$1; shift
  [ -f $m ] && return 0
  echo "$(date +%H:%M) START $(basename $m)" >> $LOG
  "$@" >> $OUT/second/logs/$(basename $m).log 2>&1 || { echo "$(date +%H:%M) FAILED $(basename $m)" >> $LOG; exit 1; }
  touch $m; echo "$(date +%H:%M) END $(basename $m)" >> $LOG
}
# work copy: code and summer GP models as in work/A, own results/
if [ ! -d $P/src ]; then
  mkdir -p $P/models $P/results
  cp -r $PKG/code/src $PKG/code/config $P/; ln -s $PKG/code/data $P/data
  cp $WORK/A/models/*.joblib $P/models/
fi
export KAIROS_ROOT=$P STUDY_RESULTS=$OUT/second
export KAIROS_SCEN='{"mpc": {"architecture": "renewable_buffer"}, "replay": {"plant": "physical"}}'
cd $P
for c in det det_bo ua; do
  step tune_$c python3 src/tune_mpc.py --controller $c --trials 30 --episodes 4 --episode-days 2 --workers 2 --resume \
    --set SoC.E_capacity=72 --set degradation.c_deg=2e-5 --set mpc.architecture=renewable_buffer \
    --set replay.plant=physical --set 'tuning.objectives=["net_cost_eur","violation_pp_h","curtailed_pct"]'
done
step fronts python3 $N/front_dev.py
step fronts_csv python3 $N/fronts_csv.py
step prereg python3 $R/stages_second.py prereg
mkdir -p $STUDY_RESULTS/tuning && cp $P/results/tuning/*.{json,csv} $STUDY_RESULTS/tuning/
[ -n "$SELECT_ONLY" ] && { echo "$(date +%H:%M) SELECTION DONE" >> $LOG; exit 0; }
[ -f $PKG/results/redo/preregistration_R2.json ] || { echo "write record R2 first (prereg_r2.py)"; exit 1; }
step map python3 $R/stages_second.py map_recovery
step final python3 $R/stages_second.py final
step t2 python3 $R/run_t2_redo.py
echo "$(date +%H:%M) SECOND DONE" >> $LOG
