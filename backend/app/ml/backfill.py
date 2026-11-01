"""Backfill historical predictions (demo / after activating a new model).

Replays the active model over past data at fixed steps, exactly as if it had been
running live: features are computed only from data available at each step (all
features are backward-looking rolling windows), and alerts are raised whenever
the risk level increases — giving a realistic risk trend and alert history.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import log_extra
from app.db.models import Equipment, Prediction
from app.ml.data_access import hourly_for_equipment, service_times
from app.ml.features import HISTORY_HOURS, compute_features
from app.ml.registry import get_active
from app.ml.scoring import get_thresholds, maybe_alert, score_feature_row

logger = logging.getLogger(__name__)


def backfill(db: Session, *, days: int = 30, step_hours: int = 12) -> dict[str, int]:
    loaded = get_active(db)
    thresholds = get_thresholds(db)
    total = 0
    for eq in db.scalars(select(Equipment).where(Equipment.status != "retired")):
        hourly = hourly_for_equipment(db, eq.id, hours=days * 24 + HISTORY_HOURS)
        if hourly.empty:
            continue
        feats = compute_features(
            hourly,
            type_code=eq.equipment_type.code,
            install_date=eq.install_date,
            service_times=service_times(db, eq.id),
        )
        end = feats.index.max()
        observed_cum = hourly.notna().any(axis=1).reindex(feats.index, fill_value=False).cumsum()
        steps = feats[
            (feats.index > end - timedelta(days=days)) & (feats.index.hour % step_hours == 0) & feats["_valid"]
        ]
        db.execute(
            text("DELETE FROM predictions WHERE equipment_id = :e AND as_of > :t"),
            {"e": eq.id, "t": (end - timedelta(days=days)).to_pydatetime()},
        )
        previous: str | None = None
        for ts in steps.index:
            r = score_feature_row(
                loaded.bundle,
                feats.loc[[ts]],
                history_hours=int(observed_cum.loc[ts]),
                thresholds=thresholds,
                now=pd.Timestamp(ts),
            )
            pred = Prediction(
                equipment_id=eq.id,
                model_version_id=loaded.version.id,
                as_of=ts.to_pydatetime(),
                failure_probability=r.failure_probability,
                health_score=r.health_score,
                risk_level=r.risk_level,
                rul_days=r.rul_days,
                anomaly_score=r.anomaly_score,
                explanation=r.explanation,
                recommendations=r.recommendations,
            )
            db.add(pred)
            db.flush()
            alert = maybe_alert(db, eq, pred, previous, loaded.version.horizon_days)
            if alert is not None:
                alert.created_at = ts.to_pydatetime()
            previous = r.risk_level
            total += 1
    logger.info("predictions backfilled", extra=log_extra(count=total, days=days))
    return {"predictions": total}
