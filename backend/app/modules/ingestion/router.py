import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.api.deps import DB, Engineer
from app.core.config import get_settings
from app.core.errors import NotFound, UnprocessableData
from app.db.models import IngestionBatch
from app.jobs import queue
from app.ml import registry
from app.modules.equipment.service import get_equipment
from app.modules.ingestion import service, uploads

router = APIRouter(tags=["ingestion"])

ALLOWED_CSV_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "text/plain",
    "application/octet-stream",
}
READ_CHUNK = 1024 * 1024


class ReadingIn(BaseModel):
    sensor: str = Field(min_length=1, max_length=50)
    ts: AwareDatetime
    value: float


class BatchIn(BaseModel):
    readings: list[ReadingIn] = Field(min_length=1)


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source: str
    filename: str | None
    status: str
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    rows_duplicate: int
    errors: list[dict[str, Any]]
    created_at: datetime
    finished_at: datetime | None


@router.post("/equipment/{equipment_id}/readings:batch", response_model=BatchOut, status_code=202)
def ingest_readings(equipment_id: uuid.UUID, body: BatchIn, db: DB, user: Engineer) -> IngestionBatch:
    """Ingest up to ``PDM_MAX_BATCH_READINGS`` readings. Invalid rows are rejected and
    reported; valid rows are stored. A re-score is queued if a model is active."""
    limit = get_settings().max_batch_readings
    if len(body.readings) > limit:
        raise UnprocessableData(f"At most {limit} readings per batch")
    eq = get_equipment(db, equipment_id)
    batch = service.ingest_batch(db, eq, [(r.sensor, r.ts, r.value) for r in body.readings], user.id)
    if batch.rows_accepted and registry.has_active(db):
        queue.enqueue(db, "score_equipment", {"equipment_id": str(eq.id)}, dedupe=True)
    db.commit()
    return batch


@router.post("/ingestion/csv", response_model=BatchOut, status_code=202)
async def upload_csv(file: Annotated[UploadFile, File()], db: DB, user: Engineer) -> IngestionBatch:
    """Upload a CSV (``equipment_serial,sensor,ts,value``). The file is streamed to
    disk with a size limit, the header is checked immediately, and the rows are
    imported by the background worker. Poll ``GET /ingestion/batches/{id}``."""
    s = get_settings()
    name = file.filename or "upload.csv"
    if not name.lower().endswith(".csv"):
        raise UnprocessableData("Only .csv files are accepted")
    if file.content_type and file.content_type.split(";")[0] not in ALLOWED_CSV_TYPES:
        raise UnprocessableData(f"Unsupported content type {file.content_type}")
    first = await file.read(4096)
    header_error = service.validate_csv_header(first.split(b"\n", 1)[0])
    if header_error:
        raise UnprocessableData(header_error)
    batch = IngestionBatch(source="csv", filename=name[:255], status="pending", user_id=user.id)
    db.add(batch)
    db.flush()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    path = s.upload_dir / f"{batch.id}.csv"  # server-generated name; user filename never touches the filesystem
    max_bytes = s.max_upload_mb * 1024 * 1024
    size = len(first)
    try:
        with path.open("wb") as out:
            out.write(first)
            while chunk := await file.read(READ_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UnprocessableData(f"File exceeds {s.max_upload_mb} MB limit")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        db.rollback()
        raise
    location = uploads.publish(path, str(batch.id))  # shared volume locally, S3 on AWS
    queue.enqueue(db, "csv_import", {"batch_id": str(batch.id), **location})
    db.commit()
    return batch


@router.get("/ingestion/batches", response_model=list[BatchOut])
def list_batches(db: DB, _: Engineer, limit: int = 20) -> list[IngestionBatch]:
    from sqlalchemy import select

    return list(db.scalars(select(IngestionBatch).order_by(IngestionBatch.created_at.desc()).limit(min(limit, 100))))


@router.get("/ingestion/batches/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: uuid.UUID, db: DB, _: Engineer) -> IngestionBatch:
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        raise NotFound("Batch not found")
    return batch
