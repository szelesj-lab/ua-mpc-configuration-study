"""Data inventory of the study: coverage, resolution, gaps, use. Writes inventory.csv, energy.csv."""
import glob, os, sys, warnings
import pandas as pd
warnings.filterwarnings("ignore")
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))          # shipped results (input of the analysis)
K = os.environ.get("KAIROS_CODE", os.path.join(PKG, "code"))          # controller code
OUTD = os.environ.get("OUT", os.path.join(PKG, "out"))                 # generated tables and figures
os.makedirs(f"{RES}/analysis", exist_ok=True)
os.chdir(f"{RES}/analysis")
sys.path.insert(0, f"{K}/src")
from prepare_load_profile import read_meter, to_utc, fill
R = os.environ["COMPANY_RAW"]   # the company export (not public); the outputs are shipped in results/analysis
TZ = "Europe/Budapest"
rows = []

def add(**k): rows.append(k)

# main meter
raw = read_meter(f"{R}/01_Fomero")
full = pd.date_range(raw.index.min(), raw.index.max(), freq="h")
utc = to_utc(raw)
h, miss = fill(utc)
zeros = int((raw <= 0).sum()); nan = int(raw.isna().sum())
absent = len(full) - len(raw)
add(source="Main meter (plant)", origin="Company export, 4 quarterly xlsx", start=raw.index.min(), end=raw.index.max(),
    resolution="1 h, kWh", records=len(raw), gaps=f"{zeros} zero, {nan} empty, {absent} absent, DST hours; {int(miss.sum())} h filled ({100*miss.mean():.2f} %)",
    used="Load profile of all replays (per unit, scaled to the cell)")
# sub-meters, production, gas, context: inventoried, not used
for area in sorted(glob.glob(f"{R}/02_Villamos_almerek/*")):
    fs = sorted(glob.glob(f"{area}/*.xlsx"))
    meters = sorted({os.path.basename(f).split("_")[0].split(" - ")[0] for f in fs})
    add(source=f"Sub-meters: {os.path.basename(area)}", origin=f"{len(fs)} xlsx, {len(meters)} meters",
        start="2025-02 / 2025-08 / 2025-10", end="(one month each)", resolution="1 h, kWh", records="",
        gaps="partial months; outliers in Feb (company note)", used="Not used (only three months)")
p = pd.read_excel(glob.glob(f"{R}/03_Termeles/*.xlsx")[0])
add(source="Production (daily counts)", origin="Company, 1 xlsx, 10 product lines", start=p.iloc[:, 0].min(), end=p.iloc[:, 0].max(),
    resolution="1 day", records=len(p), gaps="units not documented", used="Not used (perfect load forecast assumed)")
add(source="Gas", origin="Company, 3 xlsx", start="2025-02", end="2025-10", resolution="1 month, m3", records=3,
    gaps="three months", used="Not used (no CO2 KPI in this study)")
add(source="Plant state, outdoor temperature", origin="Company, 5 csv", start="2025-02", end="2025-10", resolution="irregular",
    gaps="duplicate stamps", records="", used="Not used")

def ts(path, col, tz=None):
    d = pd.read_csv(path)
    t = pd.to_datetime(d[col], utc=True) if tz is None else pd.to_datetime(d[col]).dt.tz_localize(tz)
    return d, t

d, t = ts(f"{K}/data/ENTSOE_HU_Dataset_2025.csv", "Timestamp", "UTC")
exp = pd.date_range(t.min(), t.max(), freq="15min")
add(source="Day-ahead price HU (ENTSO-E)", origin="ENTSO-E Transparency, bidding zone HU", start=t.min(), end=t.max(),
    resolution="15 min, EUR/MWh", records=len(d), gaps=f"{len(exp) - t.nunique()} missing steps, {int(d['price'].isna().sum())} empty",
    used="Energy price of every replay (import only, no export)")
for f, name, use in [("Past_Forecast_Values.csv", "Weather forecast as issued (Open-Meteo historical forecast)",
                      "GPR input; forecasts mu, sigma"),
                     ("Past_Real_Values.csv", "Weather as it was (Open-Meteo reanalysis)",
                      "GPR target; 'real' PV and wind of the cell")]:
    d, t = ts(f"{K}/data/{f}", "date")
    exp = pd.date_range(t.min(), t.max(), freq="h")
    add(source=name, origin="Open-Meteo, 48.09 N 19.91 E, tilt 35 deg, south", start=t.min(), end=t.max(),
        resolution="1 h", records=len(d), gaps=f"{len(exp) - t.nunique()} missing hours", used=use)
inv = pd.DataFrame(rows)
inv.to_csv("inventory.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
print(inv.to_string(index=False))

# annual plant figures (rounded for anonymity) and per-unit scale of the cell
loc = h.tz_convert(TZ)
mean_kw = h.mean()
print(f"\nplant: {h.sum()/1e3:.0f} MWh/yr (~{round(h.sum()/1e6, 0):.0f} GWh), mean {mean_kw:.0f} kW, peak {h.max():.0f} kW, "
      f"peak/mean {h.max()/mean_kw:.2f}, min/mean {h[h>0].min()/mean_kw:.2f}")
wk = loc.groupby(loc.index.dayofweek).mean() / mean_kw
print("weekday means (pu):", wk.round(2).tolist())
print("monthly means (pu):", (loc.groupby(loc.index.month).mean() / mean_kw).round(2).to_dict())
pd.DataFrame({"pu": (h / mean_kw).values}, index=h.index).to_csv("hourly_pu.csv")
