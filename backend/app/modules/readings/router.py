"""Time-series queries with server-side downsampling.

Returning millions of raw points to a browser is slow and useless for a chart,
so readings are aggregated into buckets (avg/min/max) with PostgreSQL's
``date_bin``. ``bucket=raw`` returns raw readings (capped).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DB, Viewer
from app.core.errors import UnprocessableData
from app.modules.equipment.service import get_equipment

router = APIRouter(tags=["readings"])

BUCKETS = {"10m": "10 minutes", "1h": "1 hour", "6h": "6 hours", "1d": "1 day"}
RAW_LIMIT = 20_000
MAX_POINTS = 50_000


class Point(BaseModel):
    ts: datetime
    avg: float
    min: float
    max: float
    count: int


class Series(BaseModel):
    sensor: str
    unit: str
    bucket: str
    points: list[Point]


@router.get("/equipment/{equipment_id}/readings", response_model=list[Series])
def get_readings(
    equipment_id: uuid.UUID,
    db: DB,
    _: Viewer,
    sensor: list[str] | None = Query(default=None),
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    bucket: Literal["raw", "10m", "1h", "6h", "1d"] = "1h",
) -> list[Series]:
    eq = get_equipment(db, equipment_id)
    sensors = {s.code: s for s in eq.equipment_type.sensors}
    wanted = sensor or list(sensors)
    unknown = set(wanted) - set(sensors)
    if unknown:
        raise UnprocessableData(f"Unknown sensors for this equipment: {sorted(unknown)}")
    if end is None:
        latest = db.execute(text("SELECT max(ts) FROM sensor_readings WHERE equipment_id = :e"), {"e": eq.id}).scalar()
        end = latest or datetime.now(UTC)
    start = start or end - timedelta(days=7)
    if start >= end:
        raise UnprocessableData("'from' must be before 'to'")
    ids = [sensors[c].id for c in wanted]
    if bucket == "raw":
        sql = text(
            "SELECT sensor_id, ts AS bucket_ts, value AS v_avg, value AS v_min, value AS v_max, 1 AS n "
            "FROM sensor_readings WHERE equipment_id = :e AND sensor_id = ANY(:ids) AND ts >= :s AND ts <= :t2 "
            "ORDER BY ts LIMIT :lim"
        )
        params = {"e": eq.id, "ids": ids, "s": start, "t2": end, "lim": RAW_LIMIT}
    else:
        sql = text(
            "SELECT sensor_id, date_bin(CAST(:b AS interval), ts, TIMESTAMPTZ '2000-01-01') AS bucket_ts, "
            "avg(value) AS v_avg, min(value) AS v_min, max(value) AS v_max, count(*) AS n "
            "FROM sensor_readings WHERE equipment_id = :e AND sensor_id = ANY(:ids) AND ts >= :s AND ts <= :t2 "
            "GROUP BY 1, 2 ORDER BY 2 LIMIT :lim"
        )
        params = {"b": BUCKETS[bucket], "e": eq.id, "ids": ids, "s": start, "t2": end, "lim": MAX_POINTS}
    by_sensor: dict[int, list[Point]] = {i: [] for i in ids}
    for row in db.execute(sql, params).mappings():
        by_sensor[row["sensor_id"]].append(
            Point(ts=row["bucket_ts"], avg=row["v_avg"], min=row["v_min"], max=row["v_max"], count=row["n"])
        )
    id_to_code = {sensors[c].id: c for c in wanted}
    return [
        Series(sensor=id_to_code[i], unit=sensors[id_to_code[i]].unit, bucket=bucket, points=pts)
        for i, pts in by_sensor.items()
    ]
