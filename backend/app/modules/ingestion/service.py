"""Sensor data ingestion: validation, REST batches and CSV imports.

Validation rules (applied row by row; invalid rows are rejected and reported,
valid rows are still stored):
* sensor code must exist for the equipment's type,
* value must be finite and within the sensor's [min_valid, max_valid],
* timestamp must be timezone-aware and not more than 5 minutes in the future.
"""

import csv
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import log_extra
from app.db.models import Equipment, IngestionBatch, SensorDefinition
from app.modules.ingestion.storage import Row, bulk_insert

logger = logging.getLogger(__name__)

CSV_COLUMNS = ("equipment_serial", "sensor", "ts", "value")
MAX_REPORTED_ERRORS = 100
CSV_CHUNK = 50_000
FUTURE_TOLERANCE = timedelta(minutes=5)


@dataclass
class ValidationReport:
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    equipment_ids: set[uuid.UUID] = field(default_factory=set)

    def reject(self, row: int, message: str) -> None:
        self.rejected += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append({"row": row, "error": message})


def sensor_map(db: Session, equipment_type_id: int) -> dict[str, SensorDefinition]:
    return {
        s.code: s
        for s in db.scalars(select(SensorDefinition).where(SensorDefinition.equipment_type_id == equipment_type_id))
    }


def validate_value(
    sensors: dict[str, SensorDefinition], code: str, ts: datetime, value: float, now: datetime
) -> tuple[SensorDefinition | None, str | None]:
    sd = sensors.get(code)
    if sd is None:
        return None, f"unknown sensor '{code}' for this equipment type"
    if ts.tzinfo is None:
        return None, "timestamp must include a timezone (e.g. 2026-01-01T00:00:00Z)"
    if ts > now + FUTURE_TOLERANCE:
        return None, "timestamp is in the future"
    if not math.isfinite(value):
        return None, "value must be a finite number"
    if not (sd.min_valid <= value <= sd.max_valid):
        return None, f"value {value} outside valid range [{sd.min_valid}, {sd.max_valid}] for {code}"
    return sd, None


def ingest_batch(
    db: Session, equipment: Equipment, readings: list[tuple[str, datetime, float]], user_id: uuid.UUID | None
) -> IngestionBatch:
    """Validate and store readings for one equipment (REST endpoint). Commits."""
    now = datetime.now(UTC)
    sensors = sensor_map(db, equipment.equipment_type_id)
    report = ValidationReport(total=len(readings))
    rows: list[Row] = []
    for i, (code, ts, value) in enumerate(readings):
        sd, err = validate_value(sensors, code, ts, value, now)
        if err or sd is None:
            report.reject(i, err or "invalid")
            continue
        rows.append((equipment.id, sd.id, ts, float(value)))
    inserted, dups = bulk_insert(db, rows)
    report.accepted, report.duplicates = inserted, dups
    batch = IngestionBatch(
        source="api",
        status="completed",
        rows_total=report.total,
        rows_accepted=report.accepted,
        rows_rejected=report.rejected,
        rows_duplicate=report.duplicates,
        errors=report.errors,
        user_id=user_id,
        finished_at=datetime.now(UTC),
    )
    db.add(batch)
    db.flush()
    logger.info(
        "api batch ingested",
        extra=log_extra(equipment_id=str(equipment.id), accepted=inserted, rejected=report.rejected),
    )
    return batch


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))


def import_csv(db: Session, batch: IngestionBatch, path: Path) -> ValidationReport:
    """Stream a CSV file, validate rows and insert in chunks. Called by the worker."""
    now = datetime.now(UTC)
    report = ValidationReport()
    equipment_cache: dict[str, tuple[uuid.UUID, dict[str, SensorDefinition]] | None] = {}
    chunk: list[Row] = []

    def flush() -> None:
        ins, dup = bulk_insert(db, chunk)
        report.accepted += ins
        report.duplicates += dup
        chunk.clear()

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = set(CSV_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            report.reject(1, f"missing required columns: {sorted(missing)}")
            return report
        for line_no, rec in enumerate(reader, start=2):
            report.total += 1
            serial = (rec.get("equipment_serial") or "").strip()
            if serial not in equipment_cache:
                eq = db.scalar(select(Equipment).where(Equipment.serial_number == serial))
                equipment_cache[serial] = (eq.id, sensor_map(db, eq.equipment_type_id)) if eq else None
            cached = equipment_cache[serial]
            if cached is None:
                report.reject(line_no, f"unknown equipment serial '{serial}'")
                continue
            eq_id, sensors = cached
            try:
                ts = _parse_ts(rec["ts"])
                value = float(rec["value"])
            except (ValueError, TypeError, KeyError):
                report.reject(line_no, "unparseable timestamp or value")
                continue
            sd, err = validate_value(sensors, (rec.get("sensor") or "").strip(), ts, value, now)
            if err or sd is None:
                report.reject(line_no, err or "invalid")
                continue
            chunk.append((eq_id, sd.id, ts, value))
            report.equipment_ids.add(eq_id)
            if len(chunk) >= CSV_CHUNK:
                flush()
    flush()
    return report


def validate_csv_header(first_line: bytes) -> str | None:
    try:
        header = next(csv.reader([first_line.decode("utf-8-sig").strip()]))
    except (UnicodeDecodeError, StopIteration):
        return "File is not valid UTF-8 CSV"
    missing = set(CSV_COLUMNS) - {h.strip() for h in header}
    if missing:
        return f"CSV header is missing columns: {sorted(missing)}. Expected: {', '.join(CSV_COLUMNS)}"
    return None
