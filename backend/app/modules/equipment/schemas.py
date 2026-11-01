import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EquipmentStatus = Literal["active", "maintenance", "retired"]


class SensorDefinitionIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=20)
    min_valid: float
    max_valid: float

    @model_validator(mode="after")
    def _range(self) -> "SensorDefinitionIn":
        if self.min_valid >= self.max_valid:
            raise ValueError("min_valid must be less than max_valid")
        return self


class SensorDefinitionOut(SensorDefinitionIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class EquipmentTypeIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class EquipmentTypeOut(EquipmentTypeIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sensors: list[SensorDefinitionOut]


class EquipmentBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    manufacturer: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=100)
    install_date: date | None = None
    location: str = Field(default="", max_length=200)
    operating_conditions: dict[str, Any] = Field(default_factory=dict)
    status: EquipmentStatus = "active"

    @model_validator(mode="after")
    def _install_not_future(self) -> "EquipmentBase":
        if self.install_date and self.install_date > date.today():
            raise ValueError("install_date cannot be in the future")
        return self


class EquipmentCreate(EquipmentBase):
    equipment_type_code: str
    serial_number: str = Field(min_length=1, max_length=100)


class EquipmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    install_date: date | None = None
    location: str | None = Field(default=None, max_length=200)
    operating_conditions: dict[str, Any] | None = None
    status: EquipmentStatus | None = None


class LatestPrediction(BaseModel):
    id: uuid.UUID
    as_of: datetime
    failure_probability: float
    health_score: int
    risk_level: str
    rul_days: float | None


class EquipmentOut(EquipmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    serial_number: str
    equipment_type_code: str
    equipment_type_name: str
    created_at: datetime
    updated_at: datetime
    latest_prediction: LatestPrediction | None = None


class EquipmentPage(BaseModel):
    items: list[EquipmentOut]
    total: int
