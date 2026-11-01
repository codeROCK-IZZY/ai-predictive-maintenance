"""Default equipment types and their sensors.

Used to seed the database and by the synthetic data generator, so the generated
data always matches what the API validates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSpec:
    code: str
    name: str
    unit: str
    min_valid: float
    max_valid: float


@dataclass(frozen=True)
class EquipmentTypeSpec:
    code: str
    name: str
    description: str
    sensors: tuple[SensorSpec, ...]
    failure_modes: tuple[str, ...]


VIBRATION = SensorSpec("vibration_rms", "Vibration (RMS velocity)", "mm/s", 0.0, 50.0)
BEARING_TEMP = SensorSpec("bearing_temp", "Bearing temperature", "°C", -20.0, 200.0)
MOTOR_CURRENT = SensorSpec("motor_current", "Motor current", "A", 0.0, 500.0)
PRESSURE = SensorSpec("discharge_pressure", "Discharge pressure", "bar", 0.0, 100.0)
FLOW = SensorSpec("flow_rate", "Flow rate", "m³/h", 0.0, 2000.0)
WINDING_TEMP = SensorSpec("winding_temp", "Winding temperature", "°C", -20.0, 250.0)
IMBALANCE = SensorSpec("current_imbalance", "Phase current imbalance", "%", 0.0, 100.0)
RPM = SensorSpec("rpm", "Shaft speed", "rpm", 0.0, 10000.0)
AMBIENT = SensorSpec("ambient_temp", "Ambient temperature", "°C", -50.0, 70.0)

EQUIPMENT_TYPES: tuple[EquipmentTypeSpec, ...] = (
    EquipmentTypeSpec(
        "pump",
        "Centrifugal pump",
        "Process water / chemical transfer pump",
        (VIBRATION, BEARING_TEMP, MOTOR_CURRENT, PRESSURE, FLOW, AMBIENT),
        ("bearing_wear", "seal_leak"),
    ),
    EquipmentTypeSpec(
        "compressor",
        "Air compressor",
        "Rotary screw air compressor",
        (VIBRATION, BEARING_TEMP, MOTOR_CURRENT, PRESSURE, WINDING_TEMP, AMBIENT),
        ("bearing_wear", "seal_leak", "winding_fault"),
    ),
    EquipmentTypeSpec(
        "motor",
        "Induction motor",
        "Three-phase AC induction motor",
        (VIBRATION, BEARING_TEMP, MOTOR_CURRENT, WINDING_TEMP, IMBALANCE, RPM, AMBIENT),
        ("bearing_wear", "winding_fault"),
    ),
)

# Union of all sensor codes, in a stable order. The ML feature set is built over
# this list so one model can score every equipment type (missing sensors -> NaN).
ALL_SENSOR_CODES: tuple[str, ...] = tuple(dict.fromkeys(s.code for t in EQUIPMENT_TYPES for s in t.sensors))

TYPE_CODES: tuple[str, ...] = tuple(t.code for t in EQUIPMENT_TYPES)

FAILURE_MODE_SENSORS: dict[str, tuple[str, ...]] = {
    "bearing_wear": ("vibration_rms", "bearing_temp"),
    "seal_leak": ("discharge_pressure", "flow_rate", "motor_current"),
    "winding_fault": ("winding_temp", "current_imbalance", "motor_current"),
}
