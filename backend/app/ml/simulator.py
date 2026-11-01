"""What-if failure scenario simulator.

Takes a unit's real recent sensor history, injects a synthetic degradation
(using the same physics as the data generator) over the last N days, and scores
the modified history. Nothing is stored — this is for exploring how the model
reacts, e.g. "how early would we notice a seal leak on this pump?".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableData
from app.db.models import Equipment
from app.ml.data_access import hourly_for_equipment, service_times
from app.ml.registry import get_active
from app.ml.scoring import ScoreResult, get_thresholds, score_hourly
from app.ml.synthetic import TYPE_BY_CODE, degradation_effects


def inject_degradation(hourly: pd.DataFrame, mode: str, severity: float, ramp_days: int, seed: int = 0) -> pd.DataFrame:
    """Return a copy of ``hourly`` with degradation progressing from 0 to ``severity``
    over the final ``ramp_days`` days."""
    out = hourly.copy()
    n = min(len(out), ramp_days * 24)
    if n <= 0:
        return out
    d = np.linspace(0, 1, n) ** 2
    rng = np.random.default_rng(seed)
    values = {c: out[c].to_numpy()[-n:].astype(float) for c in out.columns}
    degradation_effects(values, mode, d, severity, rng)
    for c, v in values.items():
        out.iloc[-n:, out.columns.get_loc(c)] = v
    return out


def simulate(db: Session, eq: Equipment, mode: str, severity: float, ramp_days: int) -> tuple[ScoreResult, ScoreResult]:
    spec = TYPE_BY_CODE.get(eq.equipment_type.code)
    if spec is None or mode not in spec.failure_modes:
        allowed = list(spec.failure_modes) if spec else []
        raise UnprocessableData(f"Failure mode '{mode}' is not applicable to this equipment type. Allowed: {allowed}")
    hourly = hourly_for_equipment(db, eq.id)
    if hourly.empty:
        raise UnprocessableData("No sensor data for this equipment")
    loaded = get_active(db)
    kwargs = dict(
        type_code=eq.equipment_type.code,
        install_date=eq.install_date,
        services=service_times(db, eq.id),
        thresholds=get_thresholds(db),
    )
    before = score_hourly(loaded.bundle, hourly, **kwargs)
    after = score_hourly(loaded.bundle, inject_degradation(hourly, mode, severity, ramp_days), **kwargs)
    return before, after
