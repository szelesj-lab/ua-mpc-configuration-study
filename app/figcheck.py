"""Compare each regenerated figure with the figure printed in the paper.

The reference images in app/reference_figures/ are the paper's figures, reduced to 900 px width
(made by app/tools/make_reference_figures.py). A regenerated figure is reduced the same way and both are compared pixel by
pixel in greyscale. The figure scripts run with a fixed text rendering (app/matplotlibrc), so identical software gives
identical images on every platform; a figure counts as reproduced when at most 1 % of its pixels differ by more than
8 grey levels.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.catalogue import FIGURES
from app.pipeline import OUT

REF = Path(__file__).with_name("reference_figures")
WIDTH = 900
TOL_SHARE = 1.0  # % of pixels
TOL_GREY = 8


def reduce(path: Path) -> np.ndarray:
    im = Image.open(path).convert("L")
    h = round(im.height * WIDTH / im.width)
    return np.asarray(im.resize((WIDTH, h), Image.LANCZOS), dtype=float)


def check_figures() -> list[dict]:
    out = []
    for no, title, name in FIGURES:
        new, ref = OUT / "figures" / f"{name}.png", REF / f"{name}.png"
        row = {"no": no, "title": title, "file": name, "generated": new.exists(), "reference": ref.exists(), "ok": False,
               "differing_pct": None, "note": ""}
        if not new.exists():
            row["note"] = "not generated"
        elif not ref.exists():
            row["note"] = "no reference image"
        else:
            a, b = reduce(new), np.asarray(Image.open(ref).convert("L"), dtype=float)
            if a.shape != b.shape:
                row["note"] = f"size differs: {a.shape} vs {b.shape}"
            else:
                share = 100 * float((np.abs(a - b) > TOL_GREY).mean())
                row.update(differing_pct=round(share, 3), ok=share <= TOL_SHARE)
        out.append(row)
    return out
