#!/bin/bash
# Build the working tree that the scripts of v1.1.0 expect ($UA_WORK), from the package, with symbolic links.
# The scripts are the versions that produced results/extended; only their absolute paths were replaced by
# UA_WORK / UA_CODE / UA_KAIROS. Outputs are written into the linked result folders.
set -e
PKG=$(cd "$(dirname "$0")/../.." && pwd)
export UA_WORK=${UA_WORK:-$PKG/work/extended}
E=$PKG/experiments/extended; R=$PKG/results/extended
mkdir -p "$UA_WORK"
lnall() {  # lnall <target dir> <source dir>...  : link every entry of the sources into the target
  local t=$1; shift; mkdir -p "$t"
  for s in "$@"; do for f in "$s"/*; do [ -e "$t/$(basename "$f")" ] || ln -s "$f" "$t/$(basename "$f")"; done; done
}
# summer study (engine, stages and results per configuration)
lnall "$UA_WORK/ind" "$PKG/experiments/summer"
for X in A B C; do ln -sfn "$PKG/results/summer/$X" "$UA_WORK/ind/results_$X"; done
# winter test and the stricter-solver re-run
mkdir -p "$UA_WORK/rw" "$UA_WORK/ms3"; ln -sfn "$PKG/results/winter" "$UA_WORK/rw/results"; ln -sfn "$PKG/results/analysis/extended" "$UA_WORK/ms3/rev"
# additional analyses: scripts and results side by side, as they were run
lnall "$UA_WORK/rev2/runs"  "$E/sensitivity" "$R/sensitivity"
lnall "$UA_WORK/rev2/calib" "$E/calibration" "$R/calibration"
lnall "$UA_WORK/rev2/rE"    "$E/ratio" "$R/ratio"
lnall "$UA_WORK/rev3"       "$E/preregistered" "$R/preregistered"
lnall "$UA_WORK/rev3/t12"   "$E/preregistered" "$R/preregistered"
lnall "$UA_WORK/rev3/tariff" "$E/tariffs" "$R/tariffs"
lnall "$UA_WORK/rev3/season" "$E/seasonal" "$R/seasonal"
lnall "$UA_WORK/rev4/bench" "$E/indicators" "$R/indicators"
lnall "$UA_WORK/rev5/diag"  "$E/calibration" "$R/calibration"
lnall "$UA_WORK/rev5/link"  "$E/ratio" "$R/ratio"
echo "working tree in $UA_WORK (UA_CODE=${UA_CODE:-code}, UA_KAIROS=${UA_KAIROS:-work})"
