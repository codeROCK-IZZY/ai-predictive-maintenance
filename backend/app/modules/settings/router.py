"""Admin-configurable risk thresholds."""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import DB, Admin, Viewer
from app.db.models import RiskThreshold
from app.modules import audit

router = APIRouter(prefix="/config", tags=["settings"])


class Thresholds(BaseModel):
    medium: float = Field(gt=0, lt=1)
    high: float = Field(gt=0, lt=1)
    critical: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "Thresholds":
        if not (self.medium < self.high < self.critical):
            raise ValueError("thresholds must satisfy medium < high < critical")
        return self


@router.get("/risk-thresholds", response_model=Thresholds)
def get_thresholds(db: DB, _: Viewer) -> Thresholds:
    t = {r.risk_level: r.min_probability for r in db.scalars(select(RiskThreshold))}
    return Thresholds(medium=t["medium"], high=t["high"], critical=t["critical"])


@router.put("/risk-thresholds", response_model=Thresholds)
def put_thresholds(body: Thresholds, db: DB, admin: Admin) -> Thresholds:
    """New thresholds apply to future predictions; past predictions are not re-labelled."""
    old = get_thresholds(db, admin)
    for level, value in body.model_dump().items():
        row = db.get(RiskThreshold, level)
        assert row is not None
        row.min_probability = value
    audit.record(
        db, admin.id, "config.risk_thresholds", "risk_thresholds", None, old=old.model_dump(), new=body.model_dump()
    )
    db.commit()
    return body
