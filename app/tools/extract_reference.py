"""Extract the numbers printed in the paper's tables into app/paper_reference.json.

    python -m app.tools.extract_reference manuscript.tex supplementary.tex

Run once per manuscript version; the JSON is what the reproduction compares against. For every table it keeps,
row by row, the row label and the numbers of the remaining columns exactly as printed (as strings, so the number
of decimals is kept). Scientific numbers such as 5.0\\times10^{-4} are kept as "5.0e-4".
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# number of leading label columns per table; tables not listed are not checked
LABEL_COLS = {"tab:periods": 1, "tab:weights": 2, "tab:calib": 2, "tab:final": 2, "tab:boot": 2, "tab:litind": 3,
              "tab:oos": 2, "tab:prereg": 1, "tab:t1": 1, "tab:winter": 2, "tab:t2": 1, "tab:c2final": 1,
              "tab:c2boot": 1, "tab:c2map": 3, "tab:c2var": 3, "tab:sens": 2, "tab:tariff": 1}

SCI = re.compile(r"(\d+(?:\.\d+)?)\\times10\^\{(-?\d+)\}")
NUM = re.compile(r"(?<![\w.])[+\-]?\d+(?:\.\d+)?(?:e-?\d+)?")


def clean(cell: str) -> str:
    c = cell.replace("$-$", "-").replace("−", "-").replace("$\\pm$", "").replace("{,}", "")
    c = SCI.sub(lambda m: f"{m.group(1)}e{m.group(2)}", c)
    c = re.sub(r"\\textbf\{([^}]*)\}", r"\1", c)
    for k, v in (("\\DETBO", "DET-BO"), ("\\DET", "DET"), ("\\UA", "UA-MPC"), ("$\\rho$", "ρ"), ("$\\rE$", "r_E"), ("$\\sigma_k$", "σ"),
                 ("\\,", " "), ("\\ ", " ")):
        c = c.replace(k, v)
    c = re.sub(r"\\cite\{[^}]*\}", "", c)
    c = re.sub(r"\^\{\*\}|\$\^\{\*\}\$", "", c)
    c = re.sub(r"\\[a-zA-Z]+", " ", c)
    return c.replace("$", " ").replace("--", " – ")


def tables(src: str) -> dict:
    out = {}
    for label, n in LABEL_COLS.items():
        if f"\\label{{{label}}}" not in src:
            continue
        k = src.index(f"\\label{{{label}}}")
        a = src.rfind("\\begin{table", 0, k); e = src.index("\\end{table", k)
        body = src[a:e]
        rows = []
        for raw in body[body.find("\\toprule"):].split("\\\\"):
            if "&" not in raw or "\\toprule" in raw or "\\cmidrule" in raw:
                continue
            raw = raw.replace("\\midrule", "")
            cells = raw.split("&")
            nums = [t for c in cells[n:] for t in NUM.findall(clean(c))]
            if nums:
                rows.append({"label": " ".join(clean(c).strip() for c in cells[:n]).strip(), "values": nums})
        caption = re.search(r"\\caption\{(.*?)\}\\label", body, re.S)
        out[label] = {"caption": re.sub(r"\s+", " ", clean(caption.group(1)) if caption else "").strip(), "rows": rows}
    return out


def main():
    ref = {}
    for p in sys.argv[1:]:
        ref.update(tables(re.sub(r"(?m)(?<!\\)%.*$", "", Path(p).read_text(encoding="utf-8"))))
    dst = Path(__file__).resolve().parents[1] / "paper_reference.json"
    dst.write_text(json.dumps(ref, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{sum(len(t['rows']) for t in ref.values())} rows of {len(ref)} tables -> {dst}")


if __name__ == "__main__":
    main()
