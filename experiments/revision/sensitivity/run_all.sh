#!/bin/bash
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
R=${UA_WORK:-work/revision}/rev2/runs
declare -A ARCH=([B]=self_consumption [C]=setpoint [A]=renewable_buffer)
for a in A C B; do
  export KAIROS_ROOT=${UA_KAIROS:-work}/$a STUDY_RESULTS=$R/$a
  export KAIROS_SCEN="{\"limits\": {\"p_charge_max_w\": 24.0}, \"mpc\": {\"architecture\": \"${ARCH[$a]}\"}, \"replay\": {\"demand_profile_csv\": \"industrial_load_profile.csv\", \"demand_mean_w\": 10.788, \"plant\": \"bms\"}}"
  cd $KAIROS_ROOT && python3 $R/rev2_runs.py $a $R/$a 2>&1 | grep -v -i warn || echo "FAILED $a"
done
echo ALL DONE
