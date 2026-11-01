from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from offshore_metocean.analysis import (annual_maxima, apply_review, circular_difference,
    compare, extreme_estimates, metadata_issues, run_analysis)
from offshore_metocean.models import DLCRule


def test_no_validation_withholds_design_outputs(known, cfg):
    result = run_analysis(known[0], cfg)
    assert set(result.frames["Data_quality"].status) == {"withheld"}
    assert result.frames["Marginal_extremes"].return_level.isna().all()
    assert result.frames["DLC_candidates"].wind_candidate_m_s.isna().all()


def test_cross_hindcast_only_provisional(known, cfg):
    result = run_analysis(known[0], cfg, known[1])
    assert set(result.frames["Data_quality"].status) == {"provisional"}
    joint = result.frames["Wind_Hs_T_occurrence"]
    assert joint.p_joint.sum() == pytest.approx(1)
    assert np.allclose(joint.groupby("wind_speed_bin").p_given_wind.sum(), 1)
    assert result.frames["Conditional"].occurrence.sum() == pytest.approx(1)
    dlc = result.frames["DLC_candidates"]
    normal = dlc[(dlc.family == "normal_p50") & dlc.wind_candidate_m_s.notna()]
    assert (normal.wind_candidate_m_s >= cfg.cut_in).all()
    assert (normal.wind_candidate_m_s < cfg.cut_out).all()
    assert dlc.adopted_wind_m_s.isna().all()
    assert set(dlc.mapping_status) == {"unverified"}


def test_instantaneous_hourly_is_not_ten_minute_mean(known, cfg):
    a, b = known
    a.meta = replace(a.meta, wind_statistic="instantaneous", wind_average_minutes=None)
    result = run_analysis(a, cfg, b)
    quality = result.frames["Data_quality"].set_index("variable")
    assert quality.loc["wind_speed", "status"] == "withheld"
    assert "평균시간" in quality.loc["wind_speed", "reason"]
    assert result.frames["Marginal_extremes"].query("variable == 'wind_speed'").return_level.isna().all()


def test_refined_delivery_grid_does_not_pass_native_limit(known, cfg):
    cfg.max_grid_km = 10
    a, b = known
    a.meta = replace(a.meta, delivery_grid_km=.1)
    result = run_analysis(a, cfg, b)
    assert result.frames["Data_quality"].set_index("variable").loc["wind_speed", "status"] == "withheld"


def test_observations_and_confirmed_criteria_can_be_eligible(known, cfg):
    a, observation = known
    observation.meta = replace(observation.meta, kind="observation")
    cfg.criteria_confirmed = True
    result = run_analysis(a, cfg, observation=observation)
    assert set(result.frames["Data_quality"].status) == {"eligible"}


def test_synthetic_cannot_be_adopted(demo, cfg):
    cfg.rules = [DLCRule("TEST", "test", code_reference="TEST ONLY", verified=True)]
    result = run_analysis(demo[0], cfg, demo[1])
    assert set(result.frames["Data_quality"].status) == {"demo"}
    with pytest.raises(ValueError, match="합성"):
        apply_review(result, [0], "accepted_preliminary")


def test_review_requires_confirmed_mapping_and_is_recorded(known, cfg):
    cfg.rules = [DLCRule("TEST", "test", code_reference="TEST ONLY", verified=True)]
    result = run_analysis(known[0], cfg, known[1])
    rows = result.frames["DLC_candidates"]
    index = rows[rows.wind_candidate_m_s.notna()].index[0]
    apply_review(result, [index], "accepted_preliminary")
    assert rows.loc[index, "adopted_wind_m_s"] == rows.loc[index, "wind_candidate_m_s"]
    apply_review(result, [index], "rejected")
    assert pd.isna(rows.loc[index, "adopted_wind_m_s"])


def test_incompatible_period_not_used_for_validation(known, cfg):
    a, b = known
    b.meta = replace(b.meta, period_definition="Tm01")
    result = run_analysis(a, cfg, b)
    assert result.frames["Data_quality"].set_index("variable").loc["tp", "status"] == "withheld"


def test_no_automatic_height_correction(known, cfg):
    a, b = known
    a.meta = replace(a.meta, wind_height_m=10)
    assert any("높이" in x for x in metadata_issues(a.meta, "wind_speed", cfg))
    cfg.shear_exponent, cfg.shear_reference = .14, "TEST coefficient"
    assert not any("높이" in x for x in metadata_issues(a.meta, "wind_speed", cfg))


def test_annual_coverage_rejects_partial_year_and_records_missing_year():
    index = pd.date_range("2000-01-01", "2003-01-01", freq="3h", inclusive="left", tz="UTC")
    values = pd.Series(4., index=index)
    values = values[values.index.year != 2001]
    values.loc[values.index.year == 2002] = np.nan
    annual = annual_maxima(values, 180, .8).set_index("year")
    assert annual.loc[2000, "coverage"] == 1
    assert annual.loc[2001, "coverage"] == 0
    assert not annual.loc[2002, "included"]


def test_extreme_return_levels_match_independent_gumbel_calculation(cfg):
    rng = np.random.default_rng(9)
    years = np.arange(1970, 2020)
    annual_values = stats.gumbel_r.rvs(loc=10, scale=2, size=len(years), random_state=rng)
    series = pd.Series(annual_values, index=pd.to_datetime([f"{y}-07-01" for y in years], utc=True))
    cfg.min_annual_coverage = .001
    estimates, _ = extreme_estimates(series, 525600, cfg, "hs", "provisional")
    loc, scale = stats.gumbel_r.fit(annual_values)
    expected = loc-scale*np.log(-np.log(1-1/np.array(sorted(cfg.return_periods))))
    np.testing.assert_allclose(estimates.return_level, expected)
    assert (estimates.ci_high >= estimates.ci_low).all()


def test_seed_reproduces_uncertainty(demo, cfg):
    a, _ = demo
    first, _ = extreme_estimates(a.frame.hs, 180, cfg, "hs", "demo")
    second, _ = extreme_estimates(a.frame.hs, 180, cfg, "hs", "demo")
    pd.testing.assert_frame_equal(first, second)


def test_circular_difference_around_north():
    np.testing.assert_allclose(circular_difference([359, 1], [1, 359]), [-2, 2])


def test_datetime_resolution_does_not_change_sampling_interval(known, cfg):
    a, b = known
    a.frame.index = a.frame.index.as_unit("us")
    b.frame.index = b.frame.index.as_unit("ns")
    result = run_analysis(a, cfg, b)
    assert np.allclose(result.frames["Data_quality"].observed_median_spacing_minutes, 180)
    assert set(result.frames["Data_quality"].status) == {"provisional"}


def test_disallow_provisional_without_observation(known, cfg):
    cfg.allow_provisional = False
    result = run_analysis(known[0], cfg, known[1])
    assert set(result.frames["Data_quality"].status) == {"withheld"}


def test_observation_role_not_satisfied_by_hindcast(known, cfg):
    rows = compare(known[0], known[1], cfg, "observation")
    assert not any(r["screening_pass"] for r in rows)


@pytest.mark.parametrize("value", [0, 1, float("nan"), -3])
def test_invalid_return_periods_rejected(cfg, value):
    cfg.return_periods = [value]
    with pytest.raises(ValueError):
        cfg.validate()


def test_missing_site_representativeness_withheld(known, cfg):
    a, b = known
    a.meta = replace(a.meta, site_assessment="")
    result = run_analysis(a, cfg, b)
    assert set(result.frames["Data_quality"].status) == {"withheld"}
