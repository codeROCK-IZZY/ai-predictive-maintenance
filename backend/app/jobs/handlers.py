"""Background job handlers. Each receives a DB session and the job payload, and
returns a small JSON-serialisable result stored on the job row."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Equipment, IngestionBatch
from app.jobs import queue
from app.ml import registry, scoring
from app.ml.pipeline import train_from_db
from app.modules.ingestion import uploads
from app.modules.ingestion.service import import_csv


def handle_csv_import(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    batch = db.get(IngestionBatch, uuid.UUID(payload["batch_id"]))
    if batch is None:
        raise ValueError("batch not found")
    batch.status = "processing"
    db.commit()
    try:
        with uploads.fetch(payload) as path:
            report = import_csv(db, batch, path)
    except Exception:
        db.rollback()
        batch = db.merge(batch)
        batch.status = "failed"
        batch.finished_at = datetime.now(UTC)
        db.commit()
        raise
    batch.status = "completed"
    batch.rows_total, batch.rows_accepted = report.total, report.accepted
    batch.rows_rejected, batch.rows_duplicate = report.rejected, report.duplicates
    batch.errors = report.errors
    batch.finished_at = datetime.now(UTC)
    if registry.has_active(db):
        for eq_id in report.equipment_ids:
            queue.enqueue(db, "score_equipment", {"equipment_id": str(eq_id)}, dedupe=True)
    return {"accepted": report.accepted, "rejected": report.rejected, "duplicates": report.duplicates}


def handle_score_equipment(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    eq = db.get(Equipment, uuid.UUID(payload["equipment_id"]))
    if eq is None:
        return {"skipped": "equipment deleted"}
    pred = scoring.score_equipment(db, eq)
    return {"prediction_id": str(pred.id), "risk_level": pred.risk_level}


def handle_score_all(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    loaded = registry.get_active(db)
    counts: dict[str, int] = {}
    errors = 0
    for eq in db.scalars(select(Equipment).where(Equipment.status != "retired")):
        try:
            with db.begin_nested():
                pred = scoring.score_equipment(db, eq, loaded)
            counts[pred.risk_level] = counts.get(pred.risk_level, 0) + 1
        except Exception:
            errors += 1
    return {"scored": counts, "errors": errors}


def handle_train_model(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    mv = train_from_db(
        db,
        algorithm=payload.get("algorithm", "auto"),
        compare=payload.get("compare", True),
        activate=payload.get("activate", True),
    )
    db.commit()
    if mv.is_active and payload.get("score_after", True):
        queue.enqueue(db, "score_all", {})
    return {"model_version_id": mv.id, "version": mv.version, "algorithm": mv.algorithm}


HANDLERS = {
    "csv_import": handle_csv_import,
    "score_equipment": handle_score_equipment,
    "score_all": handle_score_all,
    "train_model": handle_train_model,
}
