#!/bin/bash
# Tables, numbers and figures of the paper from the shipped results (no replay; a few minutes):
#   results/winter/*/<ctrl>_all.csv, results/analysis/*.csv  ->  out/manuscript/{numbers3.tex,tables/}, out/figures/
# Set RESULTS to use re-run results instead (same folder layout as results/).
set -e
H=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$H/../.." && pwd)
export RESULTS=${RESULTS:-$PKG/results}
WINTER_RESULTS=$RESULTS/winter SUMMER_RESULTS=$RESULTS/summer python3 $H/../winter/run_rewind.py stats > /dev/null
python3 $H/split_protection.py > /dev/null
python3 $H/make_numbers.py > /dev/null
python3 $H/figures.py
echo "tables and numbers in ${OUT:-$PKG/out}/manuscript, figures in ${OUT:-$PKG/out}/figures"
