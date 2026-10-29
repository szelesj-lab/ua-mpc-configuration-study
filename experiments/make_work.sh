#!/bin/bash
# Create the working copies of the controller code: one per summer configuration (work/A, work/B, work/C) and
# one per winter month (work/W/m1 .. m6). Each copy has its own models/ and results/ folders, because the code
# resolves them relative to src/. The summer copies use the summer GP models from $SUMMER_MODELS.
set -e
PKG=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-$PKG/work}
SUMMER_MODELS=${SUMMER_MODELS:-$PKG/models_summer}
CUTOFFS=(2025-10-01 2025-11-01 2025-12-01 2026-01-01 2026-02-01 2026-03-01)
mk() {  # mk <dir>
  mkdir -p "$1"/{models,results}
  for d in src config; do rm -rf "$1/$d"; cp -r "$PKG/code/$d" "$1/$d"; done
  [ -e "$1/data" ] || ln -s "$PKG/code/data" "$1/data"
}
for X in A B C; do
  mk "$WORK/$X"
  if [ -d "$SUMMER_MODELS" ]; then cp -n "$SUMMER_MODELS"/*.joblib "$WORK/$X/models/" 2>/dev/null || true
  else echo "note: $SUMMER_MODELS not found - put the summer GP models there (README, 'Forecast models')"; fi
done
for i in 1 2 3 4 5 6; do
  mk "$WORK/W/m$i"; echo "${CUTOFFS[$((i-1))]}" > "$WORK/W/m$i/CUTOFF"
done
echo "work copies in $WORK"
