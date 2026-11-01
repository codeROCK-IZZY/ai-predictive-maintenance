# Model card — equipment failure risk

## Purpose
Estimate, for one piece of rotating equipment (pump, compressor or induction motor), the probability that it fails within the next **7 days**, given its recent sensor history. It is intended to **prioritise inspections**, not to replace engineering judgement. Every prediction carries an explanation, and its recommendations come from fixed rules.

## Outputs
| Output | Model | Notes |
|---|---|---|
| Failure probability (7 days) | Calibrated classifier: logistic regression (default, chosen by CV) or XGBoost, plus isotonic calibration | Calibration fit on held-out units |
| Health score | `round(100 × (1 − p))` | Deterministic |
| Risk level | Admin thresholds (default 0.10 / 0.30 / 0.60) | Deterministic |
| Remaining useful life | XGBoost regressor (absolute error) on rows within 60 days of a known failure | Weak; see limitations |
| Anomaly score | Isolation Forest on z-score and slope features from healthy periods | Share of healthy history that looks less unusual; not a failure prediction |
| Explanation | Exact SHAP values (TreeSHAP for XGBoost, `coef × standardised value` for logistic regression), summed per sensor | Log-odds units |

## Training data
A synthetic fleet from `app/ml/synthetic.py`, with seed 42, 40 units and 365 days at 30-minute resolution: 4.38 M readings, 103 failures and 367 maintenance events. Degradation follows `d(t) = ((t − onset)/duration)^k` with mode-specific signatures:
- **Bearing wear:** vibration rises, followed by bearing temperature.
- **Seal leak:** pressure and flow fall while current rises.
- **Winding fault:** winding temperature and current imbalance rise.

The simulator also includes confounders: preventive maintenance that catches early faults, transient process upsets, sensor drift, and seasonal ambient temperature.

## Features (69)
For 8 signals: 24 h mean/std/max; z-scores of the 6 h/24 h/72 h means against the unit's own 30-day baseline (lagged 72 h); and 24 h/72 h trend slopes in σ per day. Plus age, days since last service, and equipment type. **Temperatures are converted to rise above ambient.** Without that, models latched onto seasonal ambient temperature, a spurious signal (this was found during development and fixed).

## Evaluation protocol
- **Label:** a failure within (t, t + 7 days]. The final 7 days of data are dropped because their labels are unknown (censored).
- **Grouped 5-fold CV by unit:** no unit appears in both train and test.
- **Time holdout:** train on data before the cutoff minus 7 days (purged to avoid label overlap), test after the cutoff.
- **Metrics:** PR-AUC (primary, since only 5 % of observations are positive), ROC-AUC, recall at precision ≥ 0.5, Brier score, a reliability table, and the failure detection rate with lead time.

Results: see [`ml/reports/evaluation.md`](../ml/reports/evaluation.md). Every model version also stores its own report, visible on the **Models** page.

## When not to trust it
- **On real equipment** before re-training and re-evaluating on that equipment's actual failure history.
- **For equipment types or failure modes** outside the catalogue (the model has never seen them).
- **With less than 7 days of history:** baseline features are missing, and the UI shows a warning.
- **When the "stale data" warning is shown:** the prediction reflects old readings.
- **For exact probabilities in the 0.1–0.8 range:** these are under-confident on the holdout.

## Ethical and safety notes
This is decision support. A low risk score never means "safe to skip mandatory inspections", and a high score should trigger human inspection, not automatic shutdown.
