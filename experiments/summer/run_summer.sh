#!/bin/bash
# Summer protocol with the plant load, configurations B (self-consumption), C (set-point), A (renewable buffer):
# equal-budget ParEGO tuning -> development fronts -> pre-registration -> decision map -> one-shot final test.
# Resumable: every step leaves a marker in $OUT/summer/done. Results go to $OUT/summer/<X> (the shipped
# results in results/summer are not touched). Many hours on 2 cores.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
N=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$N/../.." && pwd)
WORK=${WORK:-$PKG/work}
OUT=${OUT:-$PKG/out}
LOG=$OUT/summer/pipeline.log
declare -A ARCH=([B]=self_consumption [C]=setpoint [A]=renewable_buffer)
declare -A CTRL=([B]="det det_bo ua" [C]="det ua" [A]="det det_bo ua")
mkdir -p $OUT/summer/done $OUT/summer/logs
step() {  # step <marker> <command...>
  local m=$OUT/summer/done/$1; shift
  [ -f $m ] && return 0
  echo "$(date +%H:%M) START $(basename $m)" >> $LOG
  "$@" >> $OUT/summer/logs/$(basename $m).log 2>&1 || { echo "$(date +%H:%M) FAILED $(basename $m)" >> $LOG; exit 1; }
  touch $m; echo "$(date +%H:%M) END $(basename $m)" >> $LOG
}
for a in B C A; do
  arch=${ARCH[$a]}
  export KAIROS_ROOT=$WORK/$a STUDY_RESULTS=$OUT/summer/$a
  export KAIROS_SCEN="{\"limits\": {\"p_charge_max_w\": 24.0}, \"mpc\": {\"architecture\": \"$arch\"}, \"replay\": {\"demand_profile_csv\": \"industrial_load_profile.csv\", \"demand_mean_w\": 10.788, \"plant\": \"bms\"}}"
  mkdir -p $STUDY_RESULTS
  cd $KAIROS_ROOT
  for c in ${CTRL[$a]}; do
    step ${a}_tune_$c python3 src/tune_mpc.py --controller $c --trials 30 --episodes 4 --episode-days 2 --workers 2 --resume \
      --set SoC.E_capacity=72 --set degradation.c_deg=2e-5 --set limits.p_charge_max_w=24 --set mpc.architecture=$arch \
      --set replay.demand_profile_csv=industrial_load_profile.csv --set replay.demand_mean_w=10.788 \
      --set replay.plant=bms --set 'tuning.objectives=["net_cost_eur","protection_h","curtailed_pct"]'
  done
  step ${a}_fronts python3 $N/front_dev.py
  step ${a}_fronts_csv python3 $N/fronts_csv.py
  step ${a}_prereg python3 $N/stages.py prereg
  step ${a}_map python3 $N/stages.py map_recovery
  step ${a}_final python3 $N/stages.py final
  mkdir -p $STUDY_RESULTS/tuning && cp $KAIROS_ROOT/results/tuning/*.{json,csv} $STUDY_RESULTS/tuning/
done
echo "$(date +%H:%M) SUMMER DONE" >> $LOG
