"""Print what differs in a reproduction report as GitHub Actions annotations.

    python -m app.tools.ci_annotations out/report.json

Annotations appear on the public summary page of a workflow run (no sign-in needed), so a failed check shows
there which step, value, table cell or figure differs. Prints at most 40 lines; never fails itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def esc(s) -> str:
    return str(s).replace("%", "%25").replace("\r", "").replace("\n", " | ")[:900]


def main(path: str):
    p = Path(path)
    if not p.exists():
        print(f"::error title=No report::{esc(path)} was not written (the check stopped before the report)")
        return
    r = json.loads(p.read_text())
    s = r.get("summary", {})
    out = []
    for st in r.get("steps", []):
        if not st.get("ok"):
            out.append(f"::error title=Step failed: {esc(st.get('title'))}::{esc(st.get('tail', ''))[-700:]}")
    for v in r.get("values", []):
        if v.get("status") == "differs" or (v.get("ok") is False):
            out.append(f"::error title=Value differs ({esc(v.get('section'))})::{esc(v.get('statement'))}: paper "
                       f"{esc(v.get('paper'))}, reproduced {esc(v.get('reproduced'))} {esc(v.get('error', ''))}")
    for t in r.get("tables", []):
        if t.get("cells_ok") != t.get("cells") or t.get("error"):
            bad = [f"{row.get('label')}: {c.get('paper')} -> {c.get('got', c.get('reproduced'))}"
                   for row in t.get("rows", []) for c in row.get("cells", []) if not c.get("ok")][:8]
            out.append(f"::error title={esc(t.get('no'))} {esc(t.get('title'))}: {t.get('cells_ok')}/{t.get('cells')} cells::"
                       f"{esc('; '.join(bad) or t.get('error', ''))}")
    for f in r.get("figures", []):
        if not f.get("ok"):
            out.append(f"::error title={esc(f.get('no'))} {esc(f.get('title'))}::differing pixels "
                       f"{f.get('differing_pct')} %, generated {f.get('generated')}, reference {f.get('reference')} {esc(f.get('note', ''))}")
    print(f"::notice title=Reproduction::values {s.get('values_ok')}/{s.get('values')}, table cells "
          f"{s.get('cells_ok')}/{s.get('cells')}, figures {s.get('figures_ok')}/{s.get('figures')}")
    for line in out[:40]:
        print(line)
    if len(out) > 40:
        print(f"::warning title=More differences::{len(out) - 40} more, see out/report.md")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "out/report.json")
    except Exception as e:  # the annotations must never hide the real result
        print(f"::warning title=Annotation script::{esc(e)}")
