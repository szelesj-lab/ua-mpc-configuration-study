"""Command line: regenerate everything from the result files and check the paper's numbers.

    python -m app.reproduce            # all steps, then the check; writes out/report.md and out/report.json
    python -m app.reproduce --check    # only the check (after an earlier run)

Exit code 0 when every value is reproduced and every step ran.
"""
from __future__ import annotations

import argparse
import json
import sys

from app import catalogue, pipeline


def write_report(steps: list[dict], rows: list[dict]) -> str:
    n_ok = sum(r["ok"] for r in rows)
    lines = ["# Reproduction report", "",
             f"Values reproduced: {n_ok} of {len(rows)}", ""]
    if steps:
        lines += ["## Steps", "", "| Step | Result | Seconds |", "|---|---|---|"]
        lines += [f"| {s['title']} | {'ok' if s['ok'] else 'FAILED'} | {s['seconds']} |" for s in steps]
        lines.append("")
    lines += ["## Values", "", "| Section | Statement | Paper | Reproduced | |", "|---|---|---|---|---|"]
    lines += [f"| {r['section']} | {r['statement']} | {r['paper']} | {r['reproduced']} | {'✓' if r['ok'] else '✗ ' + r['error']} |" for r in rows]
    text = "\n".join(lines) + "\n"
    (pipeline.OUT / "report.md").write_text(text, encoding="utf-8")
    (pipeline.OUT / "report.json").write_text(json.dumps({"steps": steps, "values": rows}, indent=1), encoding="utf-8")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only compare, do not regenerate")
    a = ap.parse_args()
    steps = [] if a.check else pipeline.run(progress=lambda i, n, t: print(f"[{i}/{n}] {t}", flush=True))
    rows = catalogue.evaluate()
    print(write_report(steps, rows))
    return 0 if all(r["ok"] for r in rows) and all(s["ok"] for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
