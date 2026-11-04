"""The reference check applies only to the reference set-up; the reference upload maps exactly onto the cell."""
from app.own import reference as R
from app.own.profile import sizes_to_cell


def _p(**kw):
    p = {"sizes": dict(R.REF_SIZES), "initial_soc": 0.5, "fill": "data", "mode": "quick", "period": ["2025-10", "2025-11"]}
    p.update(kw)
    return p


def test_reference_sizes_map_exactly_onto_the_cell():
    c = sizes_to_cell(R.REF_SIZES, R.REF_MEAN_KW)
    assert c["factor"] == 1.0 and c["E_capacity"] == 72.0 and c["p_max_w"] == 24.0
    assert abs(c["pv_scale"] - 1) < 1e-12 and abs(c["wind_scale"] - 1) < 1e-12


def test_conditions():
    up = {"reference": True}
    assert R.conditions(_p(), up)[0]
    assert R.conditions(_p(period="final_test"), up)[0]
    assert not R.conditions(_p(), {})[0]
    for bad in (dict(fill="profile"), dict(initial_soc=0.6), dict(period=["2025-11"]), dict(mode="screen"),
                dict(sizes={**R.REF_SIZES, "battery_kwh": 0.1})):
        ok, why = R.conditions(_p(**bad), up)
        assert not ok and why


def test_uploaded_reference_csv_is_recognised():
    """The reference CSV uploaded as a file (as it is, or rescaled) maps exactly onto the reference cell."""
    from app.own import runner
    from app.own.profile import read
    txt = R.sample_csv()
    s, rep = read(txt, "kw", "UTC")
    assert R.match(s) == 1.0
    u = runner.upload(txt, "kw", "UTC", "ref.csv")
    k = 1.0 / R.REF_MEAN_KW            # the file is per unit (c = 1 kW): sizes 1 / 0.010788 times the reference upload's
    assert u["ok"] and u["reference"] and u["defaults"] == R.sizes(k)
    c = sizes_to_cell(u["defaults"], u["report"]["scale_mean_kw"])
    assert abs(c["E_capacity"] - 72.0) < 1e-9 and abs(c["p_max_w"] - 24.0) < 1e-9
    assert abs(c["pv_scale"] - 1) < 1e-9 and abs(c["wind_scale"] - 1) < 1e-9
    # the cell load is exactly the stored one: load_pu x 10.788 W
    ref = R._ref_series()
    assert float((s.reindex(ref.index) * 1000 * c["factor"] - ref * 10.788).abs().max()) < 1e-9
    assert R.conditions(_p(sizes=u["defaults"]), u["report"])[0]
    s250 = (s * 250).round(3)          # rescaled and rounded as a spreadsheet would
    assert R.match(s250) is not None and abs(R.match(s250) - 250) < 1e-3
    s2 = s.copy(); s2.iloc[1000] *= 1.5
    assert R.match(s2) is None         # one changed value: an ordinary load profile
    # the profile holds hourly values, so its hourly form is the same data and is recognised too
    assert R.match(s.resample("1h").mean().reindex(s.index).ffill()) == 1.0
    import shutil
    shutil.rmtree(runner.UPLOADS / u["id"])
