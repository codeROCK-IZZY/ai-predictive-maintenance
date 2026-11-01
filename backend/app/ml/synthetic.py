"""Synthetic industrial fleet generator.

Simulates pumps, compressors and motors with physically-motivated behaviour so
that the ML problem is realistic (learnable, but not trivial):

Healthy behaviour
    * unit-to-unit variation of baseline levels (manufacturing tolerance),
    * daily/weekly load cycles and seasonal + daily ambient temperature,
    * multiplicative sensor noise, rare spikes and missing readings.

Degradation → failure
    Each unit goes through life cycles: healthy for a random (Weibull) period,
    then a failure mode begins and progresses non-linearly
    ``d(t) = ((t - onset) / duration) ** k`` until ``d = 1`` (failure).
      * bearing_wear  → vibration grows, bearing temperature follows with lag
      * seal_leak     → pressure and flow fall, current rises to compensate
      * winding_fault → winding temperature and phase imbalance rise, spikes
    After a failure the unit is down 12–48 h (no data) and a repair is logged.

Confounders (what makes it hard)
    * preventive maintenance every ~45–75 days catches an early-stage
      degradation 60% of the time (the cycle ends without failure),
    * transient process upsets (e.g. cavitation) raise vibration for hours
      without leading to failure,
    * slow sensor drift on some units,
    * fast failures (7 days) leave little warning.

Everything is driven by one seed, so datasets are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from app.modules.equipment.catalog import EQUIPMENT_TYPES, EquipmentTypeSpec

TYPE_BY_CODE = {t.code: t for t in EQUIPMENT_TYPES}

# Typical healthy operating levels per type (before unit variation).
BASELINES: dict[str, dict[str, float]] = {
    "pump": {
        "vibration_rms": 2.2,
        "bearing_temp": 48,
        "motor_current": 42,
        "discharge_pressure": 6.5,
        "flow_rate": 120,
    },
    "compressor": {
        "vibration_rms": 3.0,
        "bearing_temp": 62,
        "motor_current": 95,
        "discharge_pressure": 8.0,
        "winding_temp": 78,
    },
    "motor": {
        "vibration_rms": 1.8,
        "bearing_temp": 52,
        "motor_current": 60,
        "winding_temp": 72,
        "current_imbalance": 1.5,
        "rpm": 1480,
    },
}
NOISE = {
    "vibration_rms": 0.05,
    "bearing_temp": 0.015,
    "motor_current": 0.02,
    "discharge_pressure": 0.02,
    "flow_rate": 0.025,
    "winding_temp": 0.015,
    "current_imbalance": 0.08,
    "rpm": 0.002,
    "ambient_temp": 0.0,
}
MANUFACTURERS = {
    "pump": [("Hydraflow", "HF-200"), ("Aquadyne", "AD-75"), ("Corvex", "CP-310")],
    "compressor": [("Airtek", "RS-55"), ("Pneumo", "PX-90"), ("Corvex", "CC-120")],
    "motor": [("Voltara", "IM-450"), ("Magnetix", "MX-37"), ("Corvex", "CM-200")],
}
SITES = ["Plant A – Line 1", "Plant A – Line 2", "Plant B – Utilities", "Plant C – Packaging"]
FAILURE_COST = {"bearing_wear": (6000, 15000), "seal_leak": (3000, 9000), "winding_fault": (9000, 25000)}


@dataclass(frozen=True)
class FleetConfig:
    n_units: int = 40
    days: int = 365
    freq_minutes: int = 30
    seed: int = 42
    end: datetime = field(default_factory=lambda: datetime.now(UTC).replace(minute=0, second=0, microsecond=0))


@dataclass
class SyntheticUnit:
    serial: str
    name: str
    type_code: str
    manufacturer: str
    model: str
    install_date: date
    location: str
    operating_conditions: dict[str, float | str]


@dataclass
class SyntheticFleet:
    config: FleetConfig
    units: list[SyntheticUnit]
    readings: pd.DataFrame  # columns: serial, sensor, ts, value
    failures: pd.DataFrame  # columns: serial, occurred_at, failure_mode
    maintenance: pd.DataFrame  # columns: serial, performed_at, maintenance_type, cost, notes
    degradation: pd.DataFrame  # ground truth per step (serial, ts, mode, d) — for analysis only


# --------------------------------------------------------------------------- effects
def degradation_effects(
    values: dict[str, np.ndarray], mode: str, d: np.ndarray, severity: float, rng: np.random.Generator
) -> None:
    """Apply a failure mode's signature in place. ``d`` is progress in [0, 1].

    Shared with the what-if simulator so simulated scenarios use the same physics.
    """
    if mode == "bearing_wear":
        if "vibration_rms" in values:
            v = values["vibration_rms"]
            values["vibration_rms"] = v * (1 + 2.5 * severity * d**1.5) + rng.normal(0, 0.3 * d * severity, len(d))
        if "bearing_temp" in values:
            lagged = pd.Series(d).ewm(halflife=48).mean().to_numpy()  # temperature lags vibration
            values["bearing_temp"] = values["bearing_temp"] + 28 * severity * lagged**1.3
        if "motor_current" in values:
            values["motor_current"] = values["motor_current"] * (1 + 0.03 * severity * d)
    elif mode == "seal_leak":
        if "discharge_pressure" in values:
            values["discharge_pressure"] = values["discharge_pressure"] * (1 - 0.3 * severity * d)
        if "flow_rate" in values:
            values["flow_rate"] = values["flow_rate"] * (1 - 0.25 * severity * d)
        if "motor_current" in values:
            values["motor_current"] = values["motor_current"] * (1 + 0.09 * severity * d)
        if "bearing_temp" in values:
            values["bearing_temp"] = values["bearing_temp"] + 5 * severity * d
    elif mode == "winding_fault":
        if "winding_temp" in values:
            spikes = (rng.random(len(d)) < 0.03 * d) * rng.uniform(5, 20, len(d))
            values["winding_temp"] = values["winding_temp"] + 35 * severity * d + spikes
        if "current_imbalance" in values:
            values["current_imbalance"] = values["current_imbalance"] + 6 * severity * d**1.2
        if "motor_current" in values:
            values["motor_current"] = values["motor_current"] * (1 + 0.06 * severity * d)
    else:
        raise ValueError(f"unknown failure mode {mode}")


def _healthy_signals(
    spec: EquipmentTypeSpec,
    base: dict[str, float],
    t_index: pd.DatetimeIndex,
    site_offset: float,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    n = len(t_index)
    hour = t_index.hour.to_numpy() + t_index.minute.to_numpy() / 60
    doy = t_index.dayofyear.to_numpy()
    weekday = t_index.dayofweek.to_numpy()
    # Load: daily cycle, lower at weekends, slow random wander (AR(1)).
    wander = np.zeros(n)
    eps = rng.normal(0, 0.01, n)
    for i in range(1, n):
        wander[i] = 0.995 * wander[i - 1] + eps[i]
    load = 0.8 + 0.12 * np.sin(2 * np.pi * (hour - 8) / 24) - 0.08 * (weekday >= 5) + wander
    load = np.clip(load, 0.4, 1.1)
    ambient = (
        15
        + site_offset
        + 10 * np.sin(2 * np.pi * (doy - 110) / 365)
        + 4 * np.sin(2 * np.pi * (hour - 9) / 24)
        + rng.normal(0, 0.8, n)
    )
    sig: dict[str, np.ndarray] = {}
    for s in spec.sensors:
        c = s.code
        if c == "ambient_temp":
            sig[c] = ambient
        elif c == "vibration_rms":
            sig[c] = base[c] * (0.85 + 0.3 * load)
        elif c == "bearing_temp":
            sig[c] = base[c] + 0.5 * (ambient - 15) + 8 * (load - 0.8)
        elif c == "motor_current":
            sig[c] = base[c] * load
        elif c == "discharge_pressure":
            sig[c] = base[c] * (0.9 + 0.15 * load)
        elif c == "flow_rate":
            sig[c] = base[c] * load
        elif c == "winding_temp":
            sig[c] = base[c] + 0.6 * (ambient - 15) + 15 * (load - 0.8)
        elif c == "current_imbalance":
            sig[c] = np.full(n, base[c])
        elif c == "rpm":
            sig[c] = base[c] * (1 - 0.01 * load)
    return sig


def _simulate_unit(
    spec: EquipmentTypeSpec,
    age_years: float,
    t_index: pd.DatetimeIndex,
    steps_per_day: int,
    rng: np.random.Generator,
) -> tuple[
    np.ndarray,
    np.ndarray,
    list[tuple[int, int, str, float]],
    list[tuple[int, str]],
    list[tuple[int, str, float, str]],
    np.ndarray,
]:
    """Event simulation.

    Returns progress ``d``, mode index per step, degradation cycles
    ``(start, end, mode, severity)``, failures, maintenance events, downtime mask.
    """
    n = len(t_index)
    d = np.zeros(n)
    cycles: list[tuple[int, int, str, float]] = []
    mode_idx = np.full(n, -1)
    down = np.zeros(n, dtype=bool)
    failures: list[tuple[int, str]] = []
    maint: list[tuple[int, str, float, str]] = []

    pm_interval = int(rng.uniform(45, 75) * steps_per_day)
    pm_times = np.arange(int(rng.uniform(0, pm_interval)), n, pm_interval)
    pm_times = np.clip(pm_times + rng.integers(-2, 3, len(pm_times)) * steps_per_day, 0, n - 1)
    for p in pm_times:
        maint.append((int(p), "preventive", float(rng.uniform(300, 900)), "Scheduled preventive maintenance"))

    hazard_scale = max(35.0, 110.0 * (1 - 0.04 * age_years))  # older units fail sooner
    t = int(rng.uniform(0, 40) * steps_per_day)  # some units start mid-life
    while t < n:
        healthy = int((10 + rng.weibull(1.5) * hazard_scale) * steps_per_day)
        onset = t + healthy
        if onset >= n:
            break
        duration = int(rng.uniform(7, 35) * steps_per_day)
        k = rng.uniform(1.5, 3.0)
        sev = rng.uniform(0.7, 1.3)
        mode_name = str(rng.choice(spec.failure_modes))
        fail_t = onset + duration
        # Does a preventive maintenance catch it early?
        caught_at = None
        for p in pm_times[(pm_times > onset) & (pm_times < fail_t)]:
            progress = ((p - onset) / duration) ** k
            if progress < 0.5 and rng.random() < 0.6:
                caught_at = int(p)
                break
        end = caught_at if caught_at is not None else min(fail_t, n)
        span = np.arange(onset, end)
        d[span] = ((span - onset) / duration) ** k
        mode_idx[span] = spec.failure_modes.index(mode_name)
        cycles.append((onset, end, mode_name, sev))
        if caught_at is not None:
            maint.append(
                (
                    caught_at,
                    "repair",
                    float(rng.uniform(800, 2500)),
                    f"Early-stage {mode_name.replace('_', ' ')} found during inspection and corrected",
                )
            )
            t = caught_at + int(rng.uniform(2, 6) * steps_per_day / 24)
            continue
        if fail_t >= n:
            break  # still degrading at the end of the data window
        failures.append((fail_t, mode_name))
        downtime = int(rng.uniform(12, 48) * steps_per_day / 24)
        down[fail_t : fail_t + downtime] = True
        lo, hi = FAILURE_COST[mode_name]
        repair_t = min(fail_t + downtime, n - 1)
        maint.append(
            (
                repair_t,
                "repair",
                float(rng.uniform(lo, hi)),
                f"Corrective repair after {mode_name.replace('_', ' ')} failure",
            )
        )
        t = fail_t + downtime
    return d, mode_idx, cycles, failures, maint, down


def generate_fleet(config: FleetConfig | None = None) -> SyntheticFleet:
    cfg = config or FleetConfig()
    rng = np.random.default_rng(cfg.seed)
    steps_per_day = 24 * 60 // cfg.freq_minutes
    n = cfg.days * steps_per_day
    start = cfg.end - timedelta(days=cfg.days)
    t_index = pd.date_range(start, periods=n, freq=f"{cfg.freq_minutes}min", tz=UTC)

    units: list[SyntheticUnit] = []
    reading_frames: list[pd.DataFrame] = []
    failure_rows: list[dict[str, object]] = []
    maint_rows: list[dict[str, object]] = []
    truth_frames: list[pd.DataFrame] = []
    type_codes = [t.code for t in EQUIPMENT_TYPES]

    for u in range(cfg.n_units):
        type_code = type_codes[u % len(type_codes)]
        spec = TYPE_BY_CODE[type_code]
        manu, model = MANUFACTURERS[type_code][int(rng.integers(len(MANUFACTURERS[type_code])))]
        age_years = float(rng.uniform(1, 12))
        site_idx = int(rng.integers(len(SITES)))
        serial = f"{type_code[:3].upper()}-{1000 + u}"
        units.append(
            SyntheticUnit(
                serial=serial,
                name=f"{spec.name} {u + 1:02d}",
                type_code=type_code,
                manufacturer=manu,
                model=model,
                install_date=(start - timedelta(days=age_years * 365)).date(),
                location=SITES[site_idx],
                operating_conditions={
                    "duty_cycle": "continuous" if rng.random() < 0.7 else "intermittent",
                    "rated_load_pct": round(float(rng.uniform(70, 95)), 1),
                },
            )
        )
        base = {k: v * rng.normal(1, 0.08) for k, v in BASELINES[type_code].items()}
        sig = _healthy_signals(spec, base, t_index, site_offset=site_idx * 1.5 - 2, rng=rng)

        d, mode_idx, cycles, fails, maint, down = _simulate_unit(spec, age_years, t_index, steps_per_day, rng)
        for c_start, c_end, mode_name, sev in cycles:
            sub = {k: v[c_start:c_end].copy() for k, v in sig.items()}
            degradation_effects(sub, mode_name, d[c_start:c_end], sev, rng)
            for k in sig:
                sig[k][c_start:c_end] = sub[k]

        # Transient process upsets: vibration/current bumps that do NOT lead to failure.
        for _ in range(int(rng.integers(3, 9)) * cfg.days // 365):
            s0 = int(rng.integers(0, n))
            length = int(rng.uniform(6, 48) * steps_per_day / 24)
            amp = rng.uniform(1.4, 2.2)
            sl = slice(s0, min(n, s0 + length))
            if "vibration_rms" in sig:
                sig["vibration_rms"][sl] *= amp
            if "motor_current" in sig:
                sig["motor_current"][sl] *= 1 + (amp - 1) * 0.15
        # Slow sensor drift on ~20% of units (a sensor problem, not a machine problem).
        if rng.random() < 0.2:
            drift_sensor = "bearing_temp"
            sig[drift_sensor] = sig[drift_sensor] + np.linspace(0, rng.uniform(3, 7), n)

        keep = ~down
        for s in spec.sensors:
            vals = sig[s.code]
            noise = NOISE[s.code]
            vals = vals * (1 + rng.normal(0, noise, n)) if noise else vals
            spikes = (rng.random(n) < 0.0005) & (s.code != "ambient_temp")
            vals = np.where(spikes, vals * rng.uniform(1.5, 2.5, n), vals)
            vals = np.clip(vals, s.min_valid, s.max_valid)
            present = keep & (rng.random(n) > 0.003)
            reading_frames.append(
                pd.DataFrame(
                    {"serial": serial, "sensor": s.code, "ts": t_index[present], "value": np.round(vals[present], 4)}
                )
            )
        for ft, mode_name in fails:
            failure_rows.append({"serial": serial, "occurred_at": t_index[ft], "failure_mode": mode_name})
        for mt, mtype, cost, notes in maint:
            maint_rows.append(
                {
                    "serial": serial,
                    "performed_at": t_index[mt],
                    "maintenance_type": mtype,
                    "cost": round(cost, 2),
                    "notes": notes,
                }
            )
        modes = np.array(["", *spec.failure_modes])[mode_idx + 1]
        truth_frames.append(pd.DataFrame({"serial": serial, "ts": t_index, "mode": modes, "d": d}))

    return SyntheticFleet(
        config=cfg,
        units=units,
        readings=pd.concat(reading_frames, ignore_index=True),
        failures=pd.DataFrame(failure_rows, columns=["serial", "occurred_at", "failure_mode"]),
        maintenance=pd.DataFrame(maint_rows, columns=["serial", "performed_at", "maintenance_type", "cost", "notes"]),
        degradation=pd.concat(truth_frames, ignore_index=True),
    )
