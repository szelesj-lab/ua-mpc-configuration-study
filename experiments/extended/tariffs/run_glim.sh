#!/bin/bash
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
declare -A ARCH=([B]=self_consumption [C]=setpoint)
for a in ${CONFIGS:-B C}; do
  export KAIROS_ROOT=${UA_KAIROS:-work}/$a
  export KAIROS_SCEN="{\"limits\": {\"p_charge_max_w\": 24.0}, \"mpc\": {\"architecture\": \"${ARCH[$a]}\"}, \"replay\": {\"demand_profile_csv\": \"industrial_load_profile.csv\", \"demand_mean_w\": 10.788, \"plant\": \"bms\"}}"
  cd $KAIROS_ROOT && python3 ${UA_WORK:-work/extended}/rev3/tariff/run_glim.py $a 2>&1 | grep --line-buffered -v -i warn
done
echo GLIM DONE
