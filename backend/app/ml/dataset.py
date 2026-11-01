"""Turn per-unit sensor histories into a labelled training set.

Label: ``y = 1`` if a failure occurs in ``(t, t + horizon]`` for observation time t.

Correctness details that matter:
* **Censoring** — observations in the last ``horizon`` days before the data ends
  are dropped: we cannot know whether a failure follows them.
* **Sampling** — one observation every ``step_hours`` (consecutive hours are
  near-duplicates and would just inflate the dataset).
* **Groups** — each row carries its unit id so validation can keep a unit's rows
  entirely in train or entirely in test.
* ``days_to_failure`` (for the RUL model) is NaN when no failure follows before
  the data ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from app.ml.features import FEATURE_NAMES, compute_features, utc_index

if TYPE_CHECKING:
    from app.ml.synthetic import SyntheticFleet


@dataclass
class UnitHistory:
    key: str
    type_code: str
    install_date: date | None
    hourly: pd.DataFrame  # index: hourly UTC timestamps, columns: sensor codes
    failure_times: pd.DatetimeIndex
    service_times: pd.DatetimeIndex


@dataclass
class TrainingSet:
    X: pd.DataFrame
    y: np.ndarray
    groups: np.ndarray
    times: pd.DatetimeIndex
    days_to_failure: np.ndarray
    data_end: datetime
    horizon_days: int

    @property
    def n_units(self) -> int:
        return len(np.unique(self.groups))

    def summary(self) -> dict[str, object]:
        return {
            "rows": len(self.y),
            "positives": int(self.y.sum()),
            "positive_rate": round(float(self.y.mean()), 4) if len(self.y) else 0.0,
            "units": self.n_units,
            "from": self.times.min().isoformat() if len(self.times) else None,
            "to": self.times.max().isoformat() if len(self.times) else None,
            "horizon_days": self.horizon_days,
        }


def long_to_hourly(readings: pd.DataFrame, key_col: str) -> dict[str, pd.DataFrame]:
    """Long readings (key, sensor, ts, value) → {key: hourly-mean wide frame}.

    Matches ``date_trunc('hour', ts)`` + ``avg(value)`` used when reading from PostgreSQL.
    """
    r = readings.assign(hour=readings["ts"].dt.floor("h"))
    agg = r.groupby([key_col, "hour", "sensor"], observed=True)["value"].mean().unstack("sensor")
    return {str(k): g.droplevel(0).sort_index() for k, g in agg.groupby(level=0)}


def build_training_set(
    units: list[UnitHistory], *, horizon_days: int = 7, step_hours: int = 6, data_end: datetime | None = None
) -> TrainingSet:
    horizon = timedelta(days=horizon_days)
    if data_end is None:
        data_end = max(u.hourly.index.max() for u in units if not u.hourly.empty)
    frames, ys, groups, dtf = [], [], [], []
    for u in units:
        feats = compute_features(
            u.hourly, type_code=u.type_code, install_date=u.install_date, service_times=u.service_times
        )
        if feats.empty:
            continue
        # Align sampling to a fixed clock (00:00, 06:00, ...) so it is deterministic.
        sampled = feats[(feats.index.hour % step_hours == 0) & feats["_valid"]]
        sampled = sampled[sampled.index <= pd.Timestamp(data_end) - horizon]
        if sampled.empty:
            continue
        t = sampled.index
        fails = utc_index(u.failure_times)
        pos = fails.searchsorted(t, side="right")  # index of the next failure strictly after t
        if len(fails):
            next_fail = pd.Series(fails[np.clip(pos, 0, len(fails) - 1)], index=t).where(pos < len(fails))
        else:
            next_fail = pd.Series(pd.NaT, index=t, dtype="datetime64[ns, UTC]")
        delta_days = (next_fail - pd.Series(t, index=t)).dt.total_seconds() / 86400
        y = (delta_days <= horizon_days).fillna(False).to_numpy().astype(int)
        frames.append(sampled[list(FEATURE_NAMES)])
        ys.append(y)
        groups.append(np.full(len(sampled), u.key))
        dtf.append(delta_days.to_numpy())
    all_x = pd.concat(frames) if frames else pd.DataFrame(columns=list(FEATURE_NAMES), index=pd.DatetimeIndex([]))
    return TrainingSet(
        X=all_x.reset_index(drop=True),
        y=np.concatenate(ys) if ys else np.array([], dtype=int),
        groups=np.concatenate(groups) if groups else np.array([]),
        times=pd.DatetimeIndex(all_x.index),
        days_to_failure=np.concatenate(dtf) if dtf else np.array([]),
        data_end=data_end,
        horizon_days=horizon_days,
    )


def fleet_to_histories(fleet: SyntheticFleet) -> list[UnitHistory]:
    """Convert an in-memory synthetic fleet into UnitHistory objects (offline experiments)."""
    hourly = long_to_hourly(fleet.readings, "serial")
    out = []
    for unit in fleet.units:
        f = fleet.failures[fleet.failures.serial == unit.serial]["occurred_at"]
        m = fleet.maintenance[fleet.maintenance.serial == unit.serial]["performed_at"]
        out.append(
            UnitHistory(
                key=unit.serial,
                type_code=unit.type_code,
                install_date=unit.install_date,
                hourly=hourly.get(unit.serial, pd.DataFrame()),
                failure_times=pd.DatetimeIndex(f),
                service_times=pd.DatetimeIndex(m),
            )
        )
    return out
