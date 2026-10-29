"""Walk-forward GPR: train the project's GPR models (src/scicomp_training.py, unchanged) on the weather
history before CUTOFF only. Run inside a separate repository copy; the models land in its models/ folder.

    cd <root> && python train_rewind.py <root> 2025-10-01
"""
import sys
import pandas as pd

root, cutoff = sys.argv[1], pd.Timestamp(sys.argv[2], tz="Europe/Budapest")
sys.path.insert(0, f"{root}/src")
_read = pd.read_csv


def read_until_cutoff(path, *a, **k):
    df = _read(path, *a, **k)
    if str(path).endswith(("Past_Forecast_Values.csv", "Past_Real_Values.csv")):
        d = pd.to_datetime(df["date"], utc=True)
        df = df[d < cutoff.tz_convert("UTC")].reset_index(drop=True)
        print(f"{str(path).split('/')[-1]}: {len(df)} rows before {cutoff.date()}", flush=True)
    return df


pd.read_csv = read_until_cutoff
import scicomp_training  # noqa: E402

scicomp_training.pd.read_csv = read_until_cutoff
scicomp_training.main()
