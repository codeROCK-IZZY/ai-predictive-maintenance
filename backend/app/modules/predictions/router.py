import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import DB, Engineer, Viewer
from app.core.errors import NotFound
from app.db.models import ModelVersion, Prediction
from app.ml import scoring, simulator
from app.modules.equipment.service import get_equipment

router = APIRouter(tags=["predictions"])


class ModelRef(BaseModel):
    id: int
    name: str
    version: int
    algorithm: str
    horizon_days: int


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    equipment_id: uuid.UUID
    as_of: datetime
    failure_probability: float
    health_score: int
    risk_level: str
    rul_days: float | None
    anomaly_score: float | None
    explanation: dict[str, Any]
    recommendations: list[dict[str, Any]]
    created_at: datetime
    model: ModelRef | None = None


class PredictionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    as_of: datetime
    failure_probability: float
    health_score: int
    risk_level: str
    rul_days: float | None
    anomaly_score: float | None


def _with_model(db: DB, p: Prediction) -> PredictionOut:
    out = PredictionOut.model_validate(p)
    mv = db.get(ModelVersion, p.model_version_id)
    if mv:
        out.model = ModelRef(
            id=mv.id, name=mv.name, version=mv.version, algorithm=mv.algorithm, horizon_days=mv.horizon_days
        )
    return out


@router.post("/equipment/{equipment_id}/predict", response_model=PredictionOut, status_code=201)
def predict_now(equipment_id: uuid.UUID, db: DB, _: Engineer) -> PredictionOut:
    """Score the equipment synchronously with the active model and store the result."""
    eq = get_equipment(db, equipment_id)
    pred = scoring.score_equipment(db, eq)
    db.commit()
    return _with_model(db, pred)


@router.get("/equipment/{equipment_id}/predictions", response_model=list[PredictionSummary])
def prediction_history(
    equipment_id: uuid.UUID,
    db: DB,
    _: Viewer,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[Prediction]:
    get_equipment(db, equipment_id)
    stmt = select(Prediction).where(Prediction.equipment_id == equipment_id)
    if start:
        stmt = stmt.where(Prediction.as_of >= start)
    if end:
        stmt = stmt.where(Prediction.as_of <= end)
    rows = list(db.scalars(stmt.order_by(Prediction.as_of.desc()).limit(limit)))
    return rows[::-1]


@router.get("/equipment/{equipment_id}/predictions/latest", response_model=PredictionOut)
def latest_prediction(equipment_id: uuid.UUID, db: DB, _: Viewer) -> PredictionOut:
    get_equipment(db, equipment_id)
    p = db.scalar(
        select(Prediction)
        .where(Prediction.equipment_id == equipment_id)
        .order_by(Prediction.as_of.desc(), Prediction.created_at.desc())
        .limit(1)
    )
    if p is None:
        raise NotFound("No predictions yet for this equipment")
    return _with_model(db, p)


@router.get("/predictions/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: uuid.UUID, db: DB, _: Viewer) -> PredictionOut:
    p = db.get(Prediction, prediction_id)
    if p is None:
        raise NotFound("Prediction not found")
    return _with_model(db, p)


class SimulationIn(BaseModel):
    failure_mode: Literal["bearing_wear", "seal_leak", "winding_fault"]
    severity: float = Field(default=1.0, ge=0.1, le=1.5)
    ramp_days: int = Field(default=7, ge=1, le=30)


class SimulationResultOut(BaseModel):
    failure_probability: float
    health_score: int
    risk_level: str
    rul_days: float | None
    top_factors: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]


class SimulationOut(BaseModel):
    scenario: SimulationIn
    baseline: SimulationResultOut
    simulated: SimulationResultOut
    note: str = "Simulation only — nothing was stored. Degradation physics are those of the synthetic generator."


def _sim(r: scoring.ScoreResult) -> SimulationResultOut:
    return SimulationResultOut(
        failure_probability=r.failure_probability,
        health_score=r.health_score,
        risk_level=r.risk_level,
        rul_days=r.rul_days,
        top_factors=r.explanation["top_factors"],
        recommendations=r.recommendations,
    )


@router.post("/equipment/{equipment_id}/simulate", response_model=SimulationOut)
def simulate(equipment_id: uuid.UUID, body: SimulationIn, db: DB, _: Viewer) -> SimulationOut:
    eq = get_equipment(db, equipment_id)
    before, after = simulator.simulate(db, eq, body.failure_mode, body.severity, body.ramp_days)
    return SimulationOut(scenario=body, baseline=_sim(before), simulated=_sim(after))
