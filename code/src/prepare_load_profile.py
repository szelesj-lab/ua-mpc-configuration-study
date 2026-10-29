"""Anonymised industrial load profile from main-meter exports (hourly kWh) -> data/industrial_load_profile.csv.

    python src/prepare_load_profile.py <folder with the main-meter .xlsx files> [--out data/industrial_load_profile.csv]

Input: xlsx files with two columns (timestamp "YYYY.MM.DD. HH:MM" in local wall time, hour start; energy of
the hour in kWh); rows that are not timestamps (e.g. "MAXIMUM ÉRTÉK", "ÖSSZEG") are skipped.

Cleaning (every changed hour is flagged in the output column "filled"):
- zeros and empty values count as missing (the plant never draws zero power);
- the autumn clock change (the repeated local hour) is not resolvable in the export, the spring one is empty;
  local times are converted to UTC with these hours left missing;
- a missing hour gets the mean of the same weekday and hour one and two weeks before and after;
  where none of those exists, linear interpolation in time.

Output: 15-minute steps in UTC, load_pu = power / mean power over the whole file (no absolute values,
no meter names), the hourly power held constant within the hour.
"""
import argparse
from pathlib import Path

import pandas as pd

TZ = "Europe/Budapest"


def read_meter(folder):
    parts = []
    for f in sorted(Path(folder).glob("*.xlsx")):
        x = pd.read_excel(f, header=None, names=["t", "kwh"], usecols=[0, 1])
        x["t"] = pd.to_datetime(x["t"], format="%Y.%m.%d. %H:%M", errors="coerce")
        parts.append(x.dropna(subset=["t"]))
    d = pd.concat(parts).drop_duplicates("t").set_index("t").sort_index()
    return pd.to_numeric(d["kwh"], errors="coerce")


def to_utc(local):
    """Local wall time -> UTC; ambiguous (autumn) and non-existent (spring) hours become NaT and are dropped."""
    idx = local.index.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    s = pd.Series(local.to_numpy(), index=idx)
    return s[s.index.notna()].tz_convert("UTC")


def fill(hourly):
    full = pd.date_range(hourly.index.min(), hourly.index.max(), freq="h", tz="UTC")
    s = hourly.reindex(full)
    s = s.where(s > 0)                          # zeros = missing
    missing = s.isna()
    loc = s.copy()
    loc.index = s.index.tz_convert(TZ)
    same = pd.concat([loc.shift(k * 168) for k in (-2, -1, 1, 2)], axis=1).mean(axis=1)
    same.index = s.index
    s = s.fillna(same).interpolate(method="time", limit_direction="both")
    return s, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "industrial_load_profile.csv"))
    a = ap.parse_args()
    raw = read_meter(a.folder)
    hourly, missing = fill(to_utc(raw))
    pu = hourly / hourly.mean()
    q = pd.DataFrame({"load_pu": pu.to_numpy(), "filled": missing.astype(int).to_numpy()}, index=pu.index)
    q = q.reindex(pd.date_range(q.index[0], q.index[-1] + pd.Timedelta(minutes=45), freq="15min", tz="UTC"),
                  method="ffill")
    q.index.name = "timestamp_utc"
    q["load_pu"] = q["load_pu"].round(5)
    q.to_csv(a.out)
    print(f"{len(raw)} hourly values, {int(missing.sum())} filled ({100 * missing.mean():.2f} %), "
          f"{q.index[0]} .. {q.index[-1]} -> {a.out}")


if __name__ == "__main__":
    main()
