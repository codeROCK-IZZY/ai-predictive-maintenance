"""Low-level reading storage: monthly partitions and COPY-based bulk insert.

Why COPY + staging table?
* ``COPY`` streams rows in PostgreSQL's bulk format — far faster than INSERTs.
* COPY cannot do ``ON CONFLICT``, so rows land in a temporary staging table first,
  then one ``INSERT ... SELECT ... ON CONFLICT DO NOTHING`` moves them, silently
  skipping duplicates (same equipment, sensor and timestamp). The difference
  between staged and inserted rows is reported as duplicates.
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

Row = tuple[uuid.UUID, int, datetime, float]


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def ensure_partitions(db: Session, start: datetime, end: datetime) -> None:
    """Create monthly partitions covering [start, end] if they don't exist."""
    month = _month_start(start.date())
    last = _month_start(end.date())
    while month <= last:
        nxt = _next_month(month)
        name = f"sensor_readings_y{month.year}m{month.month:02d}"
        # Names/bounds are generated from dates, never from user input.
        db.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF sensor_readings "
                f"FOR VALUES FROM ('{month.isoformat()}') TO ('{nxt.isoformat()}')"
            )
        )
        month = nxt


def bulk_insert(db: Session, rows: Iterable[Row]) -> tuple[int, int]:
    """Insert readings; returns ``(inserted, duplicates)``. Runs in the caller's transaction."""
    rows = list(rows)
    if not rows:
        return 0, 0
    ensure_partitions(db, min(r[2] for r in rows), max(r[2] for r in rows))
    db.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS stage_readings "
            "(equipment_id uuid, sensor_id int, ts timestamptz, value double precision) ON COMMIT DROP"
        )
    )
    db.execute(text("TRUNCATE stage_readings"))
    raw = db.connection().connection.dbapi_connection
    assert raw is not None
    with (
        raw.cursor() as cur,  # type: ignore[attr-defined]
        cur.copy("COPY stage_readings (equipment_id, sensor_id, ts, value) FROM STDIN") as copy,
    ):
        for r in rows:
            copy.write_row(r)
    result = db.execute(
        text(
            "INSERT INTO sensor_readings (equipment_id, sensor_id, ts, value) "
            "SELECT DISTINCT ON (equipment_id, sensor_id, ts) equipment_id, sensor_id, ts, value "
            "FROM stage_readings ON CONFLICT DO NOTHING"
        )
    )
    inserted = result.rowcount  # type: ignore[attr-defined]
    return inserted, len(rows) - inserted
