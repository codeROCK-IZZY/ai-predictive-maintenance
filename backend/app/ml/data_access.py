"""Read sensor history from PostgreSQL in the shape the ML code expects."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Equipment, FailureEvent, MaintenanceEvent
from app.ml.dataset import UnitHistory
from app.ml.features import HISTORY_HOURS, utc_index

HOURLY_SQL = """
SELECT r.equipment_id, date_trunc('hour', r.ts) AS hour, s.code AS sensor, avg(r.value) AS value
FROM sensor_readings r JOIN sensor_definitions s ON s.id = r.sensor_id
WHERE {where}
GROUP BY 1, 2, 3
"""


def _to_wide(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="hour", columns="sensor", values="value", aggfunc="mean")
    wide.index = pd.DatetimeIndex(wide.index).tz_convert("UTC")
    wide.columns.name = None
    return wide.sort_index()


def hourly_for_equipment(
    db: Session, equipment_id: uuid.UUID, end: datetime | None = None, hours: int = HISTORY_HOURS
) -> pd.DataFrame:
    """Hourly means for one unit over the ``hours`` before ``end`` (default: its latest reading)."""
    if end is None:
        end = db.execute(
            text("SELECT max(ts) FROM sensor_readings WHERE equipment_id = :e"), {"e": equipment_id}
        ).scalar()
        if end is None:
            return pd.DataFrame()
    start = end - timedelta(hours=hours)
    rows = db.execute(
        text(HOURLY_SQL.format(where="r.equipment_id = :e AND r.ts > :start AND r.ts <= :end")),
        {"e": equipment_id, "start": start, "end": end},
    ).all()
    return _to_wide(pd.DataFrame(rows, columns=["equipment_id", "hour", "sensor", "value"]))


def service_times(db: Session, equipment_id: uuid.UUID) -> pd.DatetimeIndex:
    ts = db.scalars(select(MaintenanceEvent.performed_at).where(MaintenanceEvent.equipment_id == equipment_id))
    return utc_index(list(ts))


def all_histories(db: Session) -> list[UnitHistory]:
    """Full history for every unit (training). One aggregate query for all readings."""
    rows = db.execute(text(HOURLY_SQL.format(where="true"))).all()
    df = pd.DataFrame(rows, columns=["equipment_id", "hour", "sensor", "value"])
    failures = pd.DataFrame(
        db.execute(select(FailureEvent.equipment_id, FailureEvent.occurred_at)).all(), columns=["equipment_id", "t"]
    )
    maint = pd.DataFrame(
        db.execute(select(MaintenanceEvent.equipment_id, MaintenanceEvent.performed_at)).all(),
        columns=["equipment_id", "t"],
    )
    out: list[UnitHistory] = []
    grouped = dict(tuple(df.groupby("equipment_id"))) if not df.empty else {}
    for eq in db.scalars(select(Equipment)):
        out.append(
            UnitHistory(
                key=str(eq.id),
                type_code=eq.equipment_type.code,
                install_date=eq.install_date,
                hourly=_to_wide(grouped[eq.id]) if eq.id in grouped else pd.DataFrame(),
                failure_times=utc_index(failures.loc[failures.equipment_id == eq.id, "t"]),
                service_times=utc_index(maint.loc[maint.equipment_id == eq.id, "t"]),
            )
        )
    return [u for u in out if not u.hourly.empty]
