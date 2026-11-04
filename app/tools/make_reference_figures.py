"""Store the paper's figures as reference images for app/figcheck.py.

    python -m app.tools.make_reference_figures <folder with the paper's figure PNGs>

Each PNG named like an entry of app.catalogue.FIGURES is reduced to 900 px width and written to
app/reference_figures/. Run once per manuscript version, from the PNGs of the figures printed in the paper.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from app.catalogue import FIGURES
from app.figcheck import REF, WIDTH


def main():
    src = Path(sys.argv[1]); REF.mkdir(exist_ok=True); n = 0
    for _no, _title, name in FIGURES:
        p = src / f"{name}.png"
        if not p.exists():
            print("missing", p); continue
        im = Image.open(p).convert("RGB")
        im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS).save(REF / f"{name}.png", optimize=True)
        n += 1
    print(f"{n} reference images in {REF}")


if __name__ == "__main__":
    main()
