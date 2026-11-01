"""Model registry API (admin)."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import DB, Admin, Viewer
from app.core.errors import NotFound
from app.db.models import Job, ModelVersion
from app.jobs import queue
from app.ml import registry
from app.modules import audit

router = APIRouter(prefix="/models", tags=["models"])


class ModelVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    version: int
    algorithm: str
    horizon_days: int
    is_active: bool
    trained_at: datetime
    metrics: dict[str, Any]
    data_snapshot: dict[str, Any]
    feature_names: list[str]


class TrainIn(BaseModel):
    algorithm: Literal["auto", "logistic_regression", "xgboost"] = "auto"
    compare: bool = True
    activate: bool = True


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_type: str
    status: str
    attempts: int
    last_error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


@router.get("", response_model=list[ModelVersionOut])
def list_models(db: DB, _: Viewer) -> list[ModelVersion]:
    return list(db.scalars(select(ModelVersion).order_by(ModelVersion.version.desc())))


@router.post("/train", response_model=JobOut, status_code=202)
def train(body: TrainIn, db: DB, admin: Admin) -> Job:
    """Queue a training job (runs in the worker; typically a few minutes)."""
    job = queue.enqueue(db, "train_model", body.model_dump(), max_attempts=1)
    assert job is not None
    audit.record(db, admin.id, "model.train", "job", str(job.id), **body.model_dump())
    db.commit()
    return job


@router.post("/{model_id}/activate", response_model=ModelVersionOut)
def activate(model_id: int, db: DB, admin: Admin) -> ModelVersion:
    mv = db.get(ModelVersion, model_id)
    if mv is None:
        raise NotFound("Model version not found")
    registry.load_version(mv)  # fail fast if the artifact is missing or corrupted
    registry.activate_version(db, mv)
    audit.record(db, admin.id, "model.activate", "model_version", str(mv.id), version=mv.version)
    queue.enqueue(db, "score_all", {}, dedupe=True)
    db.commit()
    return mv


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: DB, _: Admin) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found")
    return job


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: DB, _: Admin, limit: int = 20) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))))
