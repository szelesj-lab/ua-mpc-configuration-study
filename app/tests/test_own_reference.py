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
