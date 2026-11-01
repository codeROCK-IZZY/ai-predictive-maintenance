"""Scoring: features → probability → health score, risk level, RUL, anomaly,
explanation and recommendations; persisting predictions and raising alerts.

Health score = round(100 × (1 − calibrated failure probability)).
Risk level  = highest level whose ``min_probability`` threshold is reached
              (thresholds are admin-configurable in ``risk_thresholds``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableData
from app.core.logging import log_extra
from app.db.models import Alert, Equipment, Prediction, RiskThreshold
from app.ml import explain, recommendations
from app.ml.data_access import hourly_for_equipment, service_times
from app.ml.features import compute_features
from app.ml.registry import LoadedModel, get_active
from app.ml.training import RUL_CAP_DAYS, ModelBundle, contributions

logger = logging.getLogger(__name__)

RISK_ORDER = ("low", "medium", "high", "critical")
MIN_HISTORY_HOURS = 24
STALE_HOURS = 6  # newest data older than this → flagged as stale in the explanation


def get_thresholds(db: Session) -> dict[str, float]:
    return {t.risk_level: t.min_probability for t in db.scalars(select(RiskThreshold))}


def risk_level(p: float, thresholds: dict[str, float]) -> str:
    level = "low"
    for lvl in RISK_ORDER:
        if p >= thresholds.get(lvl, math.inf):
            level = lvl
    return level


def health_score(p: float) -> int:
    return round(100 * (1 - p))


@dataclass
class ScoreResult:
    as_of: pd.Timestamp
    failure_probability: float
    health_score: int
    risk_level: str
    rul_days: float | None
    anomaly_score: float | None
    explanation: dict[str, Any]
    recommendations: list[dict[str, Any]]


def score_hourly(
    bundle: ModelBundle,
    hourly: pd.DataFrame,
    *,
    type_code: str,
    install_date: date | None,
    services: pd.DatetimeIndex,
    thresholds: dict[str, float],
) -> ScoreResult:
    """Pure function: score the latest hour of an hourly sensor table."""
    feats = compute_features(hourly, type_code=type_code, install_date=install_date, service_times=services)
    valid = feats[feats["_valid"]]
    if valid.empty or len(hourly) < MIN_HISTORY_HOURS:
        raise UnprocessableData(f"Not enough recent sensor data to score (need at least {MIN_HISTORY_HOURS} hours)")
    observed = hourly.notna().any(axis=1)
    return score_feature_row(bundle, valid.iloc[[-1]], history_hours=int(observed.sum()), thresholds=thresholds)


def score_feature_row(
    bundle: ModelBundle,
    frame: pd.DataFrame,
    *,
    history_hours: int,
    thresholds: dict[str, float],
    now: pd.Timestamp | None = None,
) -> ScoreResult:
    """Score one already-computed feature row (a 1-row DataFrame indexed by its hour).

    Shared by live scoring and historical backfill so both produce identical output.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    as_of = frame.index[-1]
    row = frame[list(bundle.feature_names)]
    p = float(bundle.classifier.predict_proba(row)[0])
    contrib = contributions(bundle.classifier.model, row)[0]
    values = [float(v) for v in row.iloc[0].to_numpy(dtype=float)]
    factors = explain.top_factors(bundle.feature_names, values, [float(c) for c in contrib])
    rul = None
    if bundle.rul_model is not None:
        rul = float(np.clip(bundle.rul_model.predict(row)[0], 0, RUL_CAP_DAYS))
    anomaly = float(bundle.anomaly.score(row)[0])
    level = risk_level(p, thresholds)
    dss = row["days_since_service"].iloc[0]
    days_since = None if pd.isna(dss) else float(dss)
    # A suspected failure mode is only meaningful when there is elevated risk.
    mode, votes = recommendations.infer_failure_mode(factors) if level != "low" else (None, {})
    explanation = {
        "method": "shap",
        "algorithm": bundle.algorithm,
        "units": "log-odds contribution (positive = increases risk)",
        "top_factors": factors,
        "suspected_failure_mode": mode,
        "failure_mode_votes": votes,
        "data_quality": {
            "hours_of_history": history_hours,
            "baseline_available": history_hours >= 7 * 24,
            "last_reading_hour": as_of.isoformat(),
            "stale": bool(now - as_of > pd.Timedelta(hours=STALE_HOURS)),
        },
        "caveat": "Model trained on synthetic data; validate against real failure history before relying on it.",
    }
    return ScoreResult(
        as_of=as_of,
        failure_probability=round(p, 4),
        health_score=health_score(p),
        risk_level=level,
        rul_days=None if rul is None else round(rul, 1),
        anomaly_score=round(anomaly, 4),
        explanation=explanation,
        recommendations=recommendations.recommend(level, factors, days_since, anomaly),
    )


def score_equipment(db: Session, eq: Equipment, loaded: LoadedModel | None = None) -> Prediction:
    """Score one unit with the active model, store the prediction, raise an alert
    if its risk level went up. Adds to the session; the caller commits."""
    loaded = loaded or get_active(db)
    hourly = hourly_for_equipment(db, eq.id)
    if hourly.empty:
        raise UnprocessableData("No sensor data for this equipment")
    result = score_hourly(
        loaded.bundle,
        hourly,
        type_code=eq.equipment_type.code,
        install_date=eq.install_date,
        services=service_times(db, eq.id),
        thresholds=get_thresholds(db),
    )
    previous = db.scalar(
        select(Prediction)
        .where(Prediction.equipment_id == eq.id)
        .order_by(Prediction.as_of.desc(), Prediction.created_at.desc())
        .limit(1)
    )
    pred = Prediction(
        equipment_id=eq.id,
        model_version_id=loaded.version.id,
        as_of=result.as_of.to_pydatetime(),
        failure_probability=result.failure_probability,
        health_score=result.health_score,
        risk_level=result.risk_level,
        rul_days=result.rul_days,
        anomaly_score=result.anomaly_score,
        explanation=result.explanation,
        recommendations=result.recommendations,
    )
    db.add(pred)
    db.flush()
    maybe_alert(db, eq, pred, previous.risk_level if previous else None, loaded.version.horizon_days)
    logger.info(
        "equipment scored", extra=log_extra(equipment_id=str(eq.id), risk=pred.risk_level, p=pred.failure_probability)
    )
    return pred


def maybe_alert(
    db: Session, eq: Equipment, pred: Prediction, previous_risk: str | None, horizon_days: int
) -> Alert | None:
    """Alert when risk rises to medium or above (an unchanged or falling risk does not re-alert)."""
    new_rank = RISK_ORDER.index(pred.risk_level)
    old_rank = RISK_ORDER.index(previous_risk) if previous_risk else 0
    if new_rank <= old_rank or new_rank < RISK_ORDER.index("medium"):
        return None
    mode = pred.explanation.get("suspected_failure_mode")
    hint = f" Suspected: {mode.replace('_', ' ')}." if mode else ""
    alert = Alert(
        equipment_id=eq.id,
        prediction_id=pred.id,
        previous_risk=previous_risk,
        new_risk=pred.risk_level,
        message=(
            f"{eq.name} ({eq.serial_number}) risk increased from {previous_risk or 'none'} to "
            f"{pred.risk_level} (failure probability {pred.failure_probability:.0%} within "
            f"{horizon_days} days).{hint}"
        ),
    )
    db.add(alert)
    db.flush()
    return alert
