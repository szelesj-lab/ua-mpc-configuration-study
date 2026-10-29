"""Protection hours split by edge: lower (BMS cut the discharge: deficit) and upper (cut the charge: surplus)."""
import glob, os, re, pandas as pd
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))
def split(f):
    d = pd.read_csv(f, usecols=["cut_discharge_w", "cut_charge_w"])
    lo = (d.cut_discharge_w > 1e-3); up = (d.cut_charge_w > 1e-3)
    return dict(prot_lo_h=lo.sum() / 4, prot_up_h=up.sum() / 4, prot_h=(lo | up).sum() / 4)
rows = []
for X in "ABC":
    for f in glob.glob(f"{RES}/summer/{X}/final_test/steps/bms_*.csv"):
        if f.endswith("_solves.csv"): continue
        rows.append(dict(study="final", config=X, variant=os.path.basename(f)[4:-4], **split(f)))
    for f in glob.glob(f"{RES}/summer/{X}/decision_map_recovery/steps/*.csv"):
        b = os.path.basename(f)
        m = re.match(r"(det_bo|ua)_cap(\d+)_err([\d.]+)_rp(\d+)\.csv$", b)
        if m: rows.append(dict(study="map", config=X, variant=m[1], cap=int(m[2]), err=float(m[3]), rp=int(m[4]), **split(f)))
for X in "BC":
    for f in glob.glob(f"{RES}/winter/{X}/*_all.csv"):
        rows.append(dict(study="winter", config=X, variant=os.path.basename(f)[:-8], **split(f)))
r = pd.DataFrame(rows).sort_values(["study", "config", "variant", "cap", "err", "rp"])
r.to_csv(f"{RES}/analysis/protection_split.csv", index=False)
print(r[r.study != "map"].to_string(index=False))
print(r[r.study == "map"].to_string(index=False))
