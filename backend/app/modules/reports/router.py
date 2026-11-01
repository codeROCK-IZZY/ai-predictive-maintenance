"""CSV report exports (open directly in Excel / Google Sheets)."""

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.db.models import Equipment, MaintenanceEvent
from app.modules.equipment.service import latest_predictions

router = APIRouter(prefix="/reports", tags=["reports"])


def _csv_response(rows: Iterator[list[object]], filename: str) -> StreamingResponse:
    def gen() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in rows:
            # Neutralise spreadsheet formula injection (cells starting with = + - @).
            writer.writerow([f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v for v in row])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        gen(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/fleet.csv")
def fleet_report(db: DB, _: Viewer) -> StreamingResponse:
    equipment = list(db.scalars(select(Equipment).order_by(Equipment.name)))
    preds = latest_predictions(db, [e.id for e in equipment])

    def rows() -> Iterator[list[object]]:
        yield [
            "name",
            "serial_number",
            "type",
            "location",
            "status",
            "risk_level",
            "health_score",
            "failure_probability",
            "rul_days",
            "suspected_failure_mode",
            "top_recommendation",
            "as_of",
        ]
        for e in equipment:
            p = preds.get(e.id)
            yield [
                e.name,
                e.serial_number,
                e.equipment_type.code,
                e.location,
                e.status,
                p.risk_level if p else "",
                p.health_score if p else "",
                p.failure_probability if p else "",
                p.rul_days if p and p.rul_days is not None else "",
                (p.explanation.get("suspected_failure_mode") or "") if p else "",
                p.recommendations[0]["action"] if p and p.recommendations else "",
                p.as_of.isoformat() if p else "",
            ]

    return _csv_response(rows(), f"fleet-status-{datetime.now(UTC):%Y%m%d}.csv")


@router.get("/maintenance.csv")
def maintenance_report(db: DB, _: Viewer, days: int = Query(default=90, ge=1, le=3650)) -> StreamingResponse:
    since = datetime.now(UTC) - timedelta(days=days)
    q = (
        select(MaintenanceEvent, Equipment)
        .join(Equipment, Equipment.id == MaintenanceEvent.equipment_id)
        .where(MaintenanceEvent.performed_at >= since)
        .order_by(MaintenanceEvent.performed_at.desc())
    )

    def rows() -> Iterator[list[object]]:
        yield ["performed_at", "equipment", "serial_number", "type", "cost", "notes"]
        for m, e in db.execute(q):
            yield [
                m.performed_at.isoformat(),
                e.name,
                e.serial_number,
                m.maintenance_type,
                m.cost if m.cost is not None else "",
                m.notes,
            ]

    return _csv_response(rows(), f"maintenance-last-{days}d.csv")
