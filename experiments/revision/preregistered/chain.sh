#!/bin/bash
# chain.sh <exp> [<exp> ...]   runs the listed experiments one after another
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd ${UA_KAIROS:-work}/B
for e in "$@"; do python3 ${UA_WORK:-work/revision}/rev3/run_rE.py $e 2>&1 | grep --line-buffered -v -i warn; done
echo CHAIN DONE
