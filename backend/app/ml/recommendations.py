"""Deterministic maintenance recommendation rules.

Inputs: risk level, the explanation factors, days since service and the anomaly
score. Output: a list of recommendations, each labelled ``"source": "rule"`` so
users can tell they come from fixed engineering rules, not from an AI model.

Failure-mode inference: each factor that *increases* risk votes for the failure
modes associated with its sensor, weighted by its SHAP contribution.
"""

from __future__ import annotations

from typing import Any

from app.modules.equipment.catalog import FAILURE_MODE_SENSORS

ACTIONS = {
    "bearing_wear": "Inspect bearings: check lubrication, run a vibration spectrum analysis, verify alignment "
    "and balance.",
    "seal_leak": "Inspect mechanical seals and gaskets for leaks; check suction conditions and valve positions.",
    "winding_fault": "Electrical inspection: insulation-resistance (megger) test, check phase balance, terminal "
    "connections and motor cooling.",
}
URGENCY = {
    "critical": ("critical", "Act within 24 hours; consider a controlled shutdown to avoid unplanned failure."),
    "high": ("high", "Schedule within 72 hours."),
    "medium": ("medium", "Plan for the next maintenance window and increase monitoring frequency."),
}
SERVICE_OVERDUE_DAYS = 90
ANOMALY_REVIEW = 0.99


def infer_failure_mode(factors: list[dict[str, Any]]) -> tuple[str | None, dict[str, float]]:
    votes: dict[str, float] = {}
    for f in factors:
        if f["contribution"] <= 0:
            continue
        sensor = f.get("signal", f["feature"].split("__")[0]).removesuffix("_rise")
        for mode, sensors in FAILURE_MODE_SENSORS.items():
            if sensor in sensors:
                # Shared sensors (motor current) vote with half weight for each mode.
                votes[mode] = votes.get(mode, 0.0) + f["contribution"] * (1.0 if sensors[0] == sensor else 0.5)
    if not votes:
        return None, {}
    return max(votes, key=lambda m: votes[m]), {k: round(v, 4) for k, v in votes.items()}


def recommend(
    risk_level: str,
    factors: list[dict[str, Any]],
    days_since_service: float | None,
    anomaly_score: float | None,
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    mode, _ = infer_failure_mode(factors)
    if risk_level in URGENCY:
        priority, when = URGENCY[risk_level]
        action = ACTIONS.get(mode or "", "Perform a general inspection of the equipment.")
        recs.append(
            {
                "source": "rule",
                "priority": priority,
                "suspected_failure_mode": mode,
                "action": f"{action} {when}",
            }
        )
    else:
        recs.append(
            {
                "source": "rule",
                "priority": "low",
                "suspected_failure_mode": None,
                "action": "No action needed beyond routine maintenance.",
            }
        )
    if days_since_service is not None and days_since_service > SERVICE_OVERDUE_DAYS:
        recs.append(
            {
                "source": "rule",
                "priority": "medium",
                "suspected_failure_mode": None,
                "action": f"Preventive maintenance is overdue ({days_since_service:.0f} days since last "
                f"service; policy is {SERVICE_OVERDUE_DAYS}).",
            }
        )
    if anomaly_score is not None and anomaly_score >= ANOMALY_REVIEW and risk_level in ("low", "medium"):
        recs.append(
            {
                "source": "rule",
                "priority": "medium",
                "suspected_failure_mode": None,
                "action": "Unusual sensor pattern detected that the failure model does not consider high-risk. "
                "Verify sensor health and recent operating changes.",
            }
        )
    return recs
