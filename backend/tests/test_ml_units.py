"""Unit tests for the ML building blocks (no database)."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ml import explain, recommendations, scoring
from app.ml.dataset import UnitHistory, build_training_set
from app.ml.features import FEATURE_NAMES, MODEL_SIGNALS, compute_features
from app.ml.synthetic import FleetConfig, generate_fleet
from app.ml.training import (
    contributions,
    lead_time_metrics,
    make_model,
    ranking_metrics,
    threshold_table,
)

H = pd.date_range("2026-01-01", periods=40 * 24, freq="h", tz=UTC)


def _hourly(**cols: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(cols, index=H[: len(next(iter(cols.values())))])


# ---------------------------------------------------------------- features
def test_feature_names_stable():
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES)) == 8 * len(MODEL_SIGNALS) + 5
    assert "ambient_temp__mean_24h" not in FEATURE_NAMES  # ambient is not a model input
    assert "bearing_temp_rise__z_24h" in FEATURE_NAMES


def test_slope_of_linear_signal():
    n = len(H)
    f = compute_features(
        _hourly(vibration_rms=2.0 + 0.01 * np.arange(n) + np.sin(np.arange(n)) * 1e-3),
        type_code="pump",
        install_date=None,
    )
    last = f.iloc[-1]
    base_sd = np.maximum(pd.Series(2.0 + 0.01 * np.arange(n)).rolling(720).std().shift(72).iloc[-1], 0)
    # slope is 0.01/h = 0.24/day, expressed in baseline sigmas per day
    assert last["vibration_rms__slope_72h"] == pytest.approx(0.24 / base_sd, rel=0.05)


def test_z_score_detects_step_change():
    n = len(H)
    rng = np.random.default_rng(0)
    x = 2.0 + rng.normal(0, 0.05, n)
    x[-24:] += 0.5  # step change of 10 sigma in the last day
    f = compute_features(_hourly(vibration_rms=x), type_code="pump", install_date=None).iloc[-1]
    assert f["vibration_rms__z_24h"] > 7
    assert f["vibration_rms__z_6h"] > f["vibration_rms__z_72h"]


def test_ambient_compensation_removes_weather():
    n = len(H)
    ambient = 15 + 10 * np.sin(np.arange(n) / 50)
    f = compute_features(
        _hourly(bearing_temp=40 + ambient, ambient_temp=ambient), type_code="pump", install_date=None
    ).iloc[-1]
    assert f["bearing_temp_rise__mean_24h"] == pytest.approx(40, abs=1e-6)
    assert abs(f["bearing_temp_rise__z_24h"]) < 1e-6


def test_missing_sensors_are_nan_and_context_features():
    f = compute_features(
        _hourly(vibration_rms=np.full(len(H), 2.0)),
        type_code="motor",
        install_date=date(2025, 1, 1),
        service_times=pd.DatetimeIndex([H[100]]),
    ).iloc[-1]
    assert np.isnan(f["flow_rate__mean_24h"])
    assert f["type_motor"] == 1.0 and f["type_pump"] == 0.0
    assert f["age_days"] == pytest.approx((H[-1] - pd.Timestamp("2025-01-01", tz="UTC")).total_seconds() / 86400)
    assert f["days_since_service"] == pytest.approx((len(H) - 1 - 100) / 24)


def test_valid_flag_requires_recent_data():
    x = np.full(len(H), 2.0)
    x[-10:] = np.nan
    f = compute_features(_hourly(vibration_rms=x), type_code="pump", install_date=None)
    assert bool(f["_valid"].iloc[-20]) and not bool(f["_valid"].iloc[-1])


# ---------------------------------------------------------------- dataset / labels
def test_labels_horizon_and_censoring():
    n = len(H)
    unit = UnitHistory(
        "u1",
        "pump",
        None,
        _hourly(vibration_rms=np.full(n, 2.0)),
        failure_times=pd.DatetimeIndex([H[20 * 24]]),
        service_times=pd.DatetimeIndex([]),
    )
    ds = build_training_set([unit], horizon_days=7, step_hours=6)
    fail = H[20 * 24]
    for t, y, d in zip(ds.times, ds.y, ds.days_to_failure, strict=True):
        expected = 1 if (fail - timedelta(days=7)) <= t < fail else 0
        assert y == expected, t
        if t < fail:
            assert d == pytest.approx((fail - t).total_seconds() / 86400)
        else:
            assert np.isnan(d)
    assert ds.times.max() <= H[-1] - timedelta(days=7)  # censored tail dropped
    assert set(ds.groups) == {"u1"}


# ---------------------------------------------------------------- synthetic data
@pytest.fixture(scope="module")
def small_fleet():
    return generate_fleet(FleetConfig(n_units=6, days=120, seed=3, end=datetime(2026, 6, 1, tzinfo=UTC)))


def test_generator_is_reproducible(small_fleet):
    again = generate_fleet(small_fleet.config)
    pd.testing.assert_frame_equal(small_fleet.readings, again.readings)
    pd.testing.assert_frame_equal(small_fleet.failures, again.failures)


def test_generator_respects_sensor_ranges(small_fleet):
    from app.modules.equipment.catalog import EQUIPMENT_TYPES

    limits = {s.code: (s.min_valid, s.max_valid) for t in EQUIPMENT_TYPES for s in t.sensors}
    for code, g in small_fleet.readings.groupby("sensor"):
        lo, hi = limits[code]
        assert g.value.min() >= lo and g.value.max() <= hi


def test_bearing_failures_are_preceded_by_rising_vibration():
    fleet = generate_fleet(FleetConfig(n_units=12, days=240, seed=11, end=datetime(2026, 6, 1, tzinfo=UTC)))
    bearing = fleet.failures[fleet.failures.failure_mode == "bearing_wear"]
    assert len(bearing) > 0
    r = fleet.readings[fleet.readings.sensor == "vibration_rms"]
    ratios = []
    for f in bearing.itertuples():
        u = r[r.serial == f.serial]
        before = u[(u.ts > f.occurred_at - timedelta(days=1)) & (u.ts <= f.occurred_at)].value.mean()
        healthy = u[(u.ts > f.occurred_at - timedelta(days=60)) & (u.ts <= f.occurred_at - timedelta(days=40))]
        if len(healthy):
            ratios.append(before / healthy.value.median())
    assert np.median(ratios) > 2.0


def test_no_readings_during_downtime(small_fleet):
    for f in small_fleet.failures.itertuples():
        u = small_fleet.readings[(small_fleet.readings.serial == f.serial)]
        assert not ((u.ts > f.occurred_at) & (u.ts < f.occurred_at + timedelta(hours=11))).any()


# ---------------------------------------------------------------- metrics
def test_ranking_metrics_perfect_and_random():
    y = np.array([0, 0, 0, 1, 1])
    perfect = ranking_metrics(y, np.array([0.1, 0.2, 0.3, 0.8, 0.9]))
    assert perfect["pr_auc"] == pytest.approx(1.0) and perfect["roc_auc"] == pytest.approx(1.0)
    assert np.isnan(ranking_metrics(np.zeros(4), np.ones(4))["pr_auc"])


def test_threshold_table_counts():
    y = np.array([1, 1, 0, 0])
    p = np.array([0.9, 0.2, 0.5, 0.05])
    row = threshold_table(y, p, {"high": 0.3})[0]
    assert row["precision"] == 0.5 and row["recall"] == 0.5 and row["flagged_share"] == 0.5


def test_lead_time():
    t = pd.DatetimeIndex(["2026-01-01T00:00", "2026-01-03T00:00", "2026-01-05T00:00"], tz="UTC")
    dtf = np.array([6.0, 4.0, 2.0])  # all precede the same failure on Jan 7
    m = lead_time_metrics(t, np.array(["a"] * 3), dtf, np.array([0.1, 0.5, 0.9]), 0.3, 7)
    assert m == {"failures": 1, "detected": 1, "detection_rate": 1.0, "median_lead_days": 4.0}


# ---------------------------------------------------------------- SHAP correctness
def _toy_data(n=600, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, 4)), columns=["a", "b", "c", "d"])
    y = ((X.a + 0.5 * X.b + rng.normal(0, 0.5, n)) > 1.2).astype(int).to_numpy()
    return X, y


def test_linear_contributions_sum_to_logit():
    X, y = _toy_data()
    m = make_model("logistic_regression", 1.0, 0)
    m.fit(X, y)
    c = contributions(m, X.iloc[:20])
    logit = m.decision_function(X.iloc[:20])
    np.testing.assert_allclose(c.sum(axis=1) + m[-1].intercept_[0], logit, rtol=1e-6)


def test_xgboost_contributions_match_shap_library():
    shap = pytest.importorskip("shap")
    X, y = _toy_data()
    m = make_model("xgboost", 1.0, 0)
    m.set_params(n_estimators=50)
    m.fit(X, y)
    ours = contributions(m, X.iloc[:25])
    theirs = shap.TreeExplainer(m).shap_values(X.iloc[:25])
    np.testing.assert_allclose(ours, theirs, atol=1e-4)


# ---------------------------------------------------------------- explanation, rules, scoring helpers
def test_grouped_explanation_sums_offsetting_features():
    names = ["vibration_rms__z_24h", "vibration_rms__z_72h", "flow_rate__z_24h", "type_pump"]
    out = explain.top_factors(names, [4.0, 3.5, -0.2, 1.0], [7.0, -5.0, 0.5, 9.9])
    assert out[0]["signal"] == "vibration_rms" and out[0]["contribution"] == pytest.approx(2.0)
    assert out[0]["feature"] == "vibration_rms__z_24h" and "above" in out[0]["text"]
    assert all(f["signal"] != "type_pump" for f in out)  # equipment type is not an explanation


def test_describe_templates():
    assert "rising" in explain.describe("motor_current__slope_24h", 0.8)
    assert "below" in explain.describe("discharge_pressure__z_24h", -3.0)
    assert explain.describe("days_since_service", 12.4) == "12 days since the last maintenance"


def test_failure_mode_inference_and_recommendations():
    factors = [
        {"signal": "discharge_pressure", "feature": "discharge_pressure__z_24h", "contribution": 3.0},
        {"signal": "vibration_rms", "feature": "vibration_rms__z_24h", "contribution": 0.5},
    ]
    mode, _ = recommendations.infer_failure_mode(factors)
    assert mode == "seal_leak"
    recs = recommendations.recommend("critical", factors, days_since_service=120, anomaly_score=0.5)
    assert recs[0]["priority"] == "critical" and "seal" in recs[0]["action"].lower()
    assert any("overdue" in r["action"] for r in recs)
    assert all(r["source"] == "rule" for r in recs)
    low = recommendations.recommend("low", factors, days_since_service=10, anomaly_score=0.999)
    assert low[0]["priority"] == "low" and any("Unusual sensor pattern" in r["action"] for r in low)


def test_risk_levels_and_health():
    th = {"low": 0.0, "medium": 0.1, "high": 0.3, "critical": 0.6}
    assert [scoring.risk_level(p, th) for p in (0.0, 0.1, 0.29, 0.3, 0.99)] == [
        "low",
        "medium",
        "medium",
        "high",
        "critical",
    ]
    assert scoring.health_score(0.0) == 100 and scoring.health_score(0.62) == 38
