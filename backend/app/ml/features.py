"""Feature engineering — the single code path used for BOTH training and inference.

Using one function for both avoids training/serving skew (a common production bug
where the model sees differently-computed features in production than in training).

Input: an *hourly* table for one equipment unit (index = UTC hours, columns =
sensor codes, values = hourly mean). Output: one row of features per hour.

Ambient compensation: temperatures are converted to *rise above ambient*
(``bearing_temp_rise``, ``winding_temp_rise``) and ambient temperature itself is
not a model input. Without this, models learned spurious seasonal patterns
(ambient temperature ranked as a top feature although it does not cause failures).

Per model signal (NaN if the equipment type lacks the sensor):
    mean_24h, std_24h, max_24h        recent level and volatility
    z_6h, z_24h, z_72h                recent mean vs. this unit's own baseline,
                                      in baseline standard deviations
    slope_24h, slope_72h              linear trend, in baseline σ per day
The baseline is the median/std of the previous 30 days, shifted back 72 h so a
developing fault does not contaminate its own baseline.

Context: age_days, days_since_service, equipment type one-hot.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.modules.equipment.catalog import ALL_SENSOR_CODES, TYPE_CODES

AMBIENT = "ambient_temp"
NOMINAL_AMBIENT_C = 20.0  # used only if a unit has no ambient sensor
TEMPERATURE_SENSORS = ("bearing_temp", "winding_temp")
MODEL_SIGNALS: tuple[str, ...] = tuple(
    f"{c}_rise" if c in TEMPERATURE_SENSORS else c for c in ALL_SENSOR_CODES if c != AMBIENT
)

HOURS_BASELINE = 30 * 24
BASELINE_MIN = 7 * 24
BASELINE_GAP = 72
SENSOR_STATS = ("mean_24h", "std_24h", "max_24h", "z_6h", "z_24h", "z_72h", "slope_24h", "slope_72h")
CONTEXT_FEATURES = ("age_days", "days_since_service", *(f"type_{t}" for t in TYPE_CODES))
FEATURE_NAMES: tuple[str, ...] = (
    *(f"{s}__{stat}" for s in MODEL_SIGNALS for stat in SENSOR_STATS),
    *CONTEXT_FEATURES,
)
# History needed to compute every feature for the latest hour.
HISTORY_HOURS = HOURS_BASELINE + BASELINE_GAP + 24


def _rolling_slope(x: pd.Series, window: int) -> pd.Series:
    """Least-squares slope per hour over a rolling window, robust to missing values."""
    t = pd.Series(np.arange(len(x), dtype=float), index=x.index).where(x.notna())
    mp = max(3, window // 2)
    mt = t.rolling(window, min_periods=mp).mean()
    mx = x.rolling(window, min_periods=mp).mean()
    mtx = (t * x).rolling(window, min_periods=mp).mean()
    mtt = (t * t).rolling(window, min_periods=mp).mean()
    var_t = mtt - mt * mt
    return (mtx - mt * mx) / var_t.where(var_t > 1e-9)


def utc_index(values: object) -> pd.DatetimeIndex:
    """Timestamps → tz-aware UTC DatetimeIndex (also for empty inputs, which pandas makes tz-naive)."""
    idx = pd.DatetimeIndex(pd.to_datetime(values, utc=True))
    return idx.sort_values()


def model_signals(hourly: pd.DataFrame) -> pd.DataFrame:
    """Raw sensor columns → model signals (ambient-compensated temperatures)."""
    out = hourly.drop(columns=[AMBIENT], errors="ignore").copy()
    ambient = hourly[AMBIENT] if AMBIENT in hourly.columns else NOMINAL_AMBIENT_C
    for c in TEMPERATURE_SENSORS:
        if c in out.columns:
            out[f"{c}_rise"] = out.pop(c) - ambient
    return out


def compute_features(
    hourly: pd.DataFrame,
    *,
    type_code: str,
    install_date: date | None,
    service_times: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Compute features for every hour in ``hourly``.

    Returns a DataFrame indexed by hour with ``FEATURE_NAMES`` columns plus a
    boolean ``_valid`` column (at least 3 of the last 6 hours had data).
    """
    if hourly.empty:
        return pd.DataFrame(columns=[*FEATURE_NAMES, "_valid"])
    idx = pd.date_range(hourly.index.min(), hourly.index.max(), freq="h")
    h = hourly.reindex(idx)
    observed = h.notna().any(axis=1).astype(float)
    h = model_signals(h.ffill(limit=3))

    cols: dict[str, pd.Series] = {}
    nan = pd.Series(np.nan, index=idx)
    for s in MODEL_SIGNALS:
        x = h[s] if s in h.columns else nan
        if x.isna().all():
            for stat in SENSOR_STATS:
                cols[f"{s}__{stat}"] = nan
            continue
        m6 = x.rolling(6, min_periods=3).mean()
        m24 = x.rolling(24, min_periods=12).mean()
        m72 = x.rolling(72, min_periods=36).mean()
        base = x.rolling(HOURS_BASELINE, min_periods=BASELINE_MIN).median().shift(BASELINE_GAP)
        base_sd = x.rolling(HOURS_BASELINE, min_periods=BASELINE_MIN).std().shift(BASELINE_GAP)
        # Floor the scale at 1% of the level so near-constant signals don't explode z-scores.
        scale = np.maximum(base_sd, 0.01 * base.abs()).where(base.notna())
        cols[f"{s}__mean_24h"] = m24
        cols[f"{s}__std_24h"] = x.rolling(24, min_periods=12).std()
        cols[f"{s}__max_24h"] = x.rolling(24, min_periods=12).max()
        cols[f"{s}__z_6h"] = (m6 - base) / scale
        cols[f"{s}__z_24h"] = (m24 - base) / scale
        cols[f"{s}__z_72h"] = (m72 - base) / scale
        cols[f"{s}__slope_24h"] = _rolling_slope(x, 24) * 24 / scale
        cols[f"{s}__slope_72h"] = _rolling_slope(x, 72) * 24 / scale

    ts = pd.Series(idx, index=idx)
    if install_date is not None:
        install = pd.Timestamp(install_date, tz="UTC")
        cols["age_days"] = (ts - install).dt.total_seconds() / 86400
    else:
        cols["age_days"] = nan
    if service_times is not None and len(service_times):
        svc = utc_index(service_times)
        pos = svc.searchsorted(idx, side="right") - 1
        last = pd.Series(svc[np.clip(pos, 0, None)], index=idx).where(pos >= 0)
        cols["days_since_service"] = (ts - last).dt.total_seconds() / 86400
    else:
        cols["days_since_service"] = nan
    for t in TYPE_CODES:
        cols[f"type_{t}"] = pd.Series(float(t == type_code), index=idx)

    out = pd.DataFrame(cols, index=idx)[list(FEATURE_NAMES)]
    out["_valid"] = observed.rolling(6, min_periods=1).sum() >= 3
    return out
