"""End-to-end training from the database: load → dataset → evaluate → train → register."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import UnprocessableData
from app.core.logging import log_extra
from app.db.models import ModelVersion
from app.ml import registry
from app.ml.data_access import all_histories
from app.ml.dataset import build_training_set
from app.ml.training import MODEL_NAMES, evaluate, train_bundle

logger = logging.getLogger(__name__)

MIN_POSITIVES = 30


def _json_safe(obj: Any) -> Any:
    """NaN/inf are not valid JSON; store them as null."""
    return json.loads(json.dumps(obj, default=str), parse_constant=lambda _: None)


def clean_nans(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_nans(v) for v in obj]
    return obj


def train_from_db(
    db: Session, *, algorithm: str = "auto", compare: bool = True, activate: bool = True, seed: int = 42
) -> ModelVersion:
    histories = all_histories(db)
    ds = build_training_set(histories, horizon_days=get_settings().prediction_horizon_days)
    if ds.y.sum() < MIN_POSITIVES:
        raise UnprocessableData(
            f"Only {int(ds.y.sum())} positive (pre-failure) observations; need at least {MIN_POSITIVES}. "
            "Record more failure events or load the demo dataset."
        )
    logger.info("training started", extra=log_extra(**ds.summary()))
    models = MODEL_NAMES if compare else (("xgboost",) if algorithm == "auto" else (algorithm,))
    if not compare and algorithm == "auto":
        algorithm = "xgboost"
    report = evaluate(ds, seed=seed, models=models, algorithm=algorithm)
    bundle = train_bundle(ds, algorithm=report["production_algorithm"], seed=seed)
    mv = registry.register(db, bundle, clean_nans(_json_safe(report)), activate=activate)
    logger.info("model registered", extra=log_extra(version=mv.version, algorithm=mv.algorithm, active=activate))
    return mv
