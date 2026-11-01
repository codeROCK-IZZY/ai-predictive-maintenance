"""Model training and leakage-safe evaluation.

Evaluation protocol (all numbers reported anywhere come from here):

1. **Grouped cross-validation** — ``GroupKFold`` by equipment unit (5 folds). A
   unit's observations are never in both train and test, so the model cannot
   score well by memorising individual machines.
2. **Time holdout** — train on observations before a cutoff (75% of the time
   range) minus the horizon (so no training label "peeks" into the test period),
   test on observations after the cutoff. Mimics deploying a model and using it
   on future data.

Models compared: rule baseline (max z-score), logistic regression, random forest,
XGBoost. The production model (logistic regression or XGBoost, whichever has
the better cross-validated PR-AUC) gets isotonic calibration fitted on held-out
units.

Metrics: PR-AUC (primary — suited to rare positives), ROC-AUC, best-F1, recall
at precision ≥ 0.5, Brier score of calibrated probabilities, precision/recall at
the risk thresholds, failure detection rate and lead time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    precision_recall_curve,
    roc_auc_score,
    root_mean_squared_error,
)
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from app.ml.dataset import TrainingSet
from app.ml.features import FEATURE_NAMES

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {"medium": 0.10, "high": 0.30, "critical": 0.60}
RUL_CAP_DAYS = 60.0
ANOMALY_STATS = ("z_6h", "z_24h", "z_72h", "slope_24h", "slope_72h")
ANOMALY_FEATURES = tuple(f for f in FEATURE_NAMES if f.split("__")[-1] in ANOMALY_STATS)


# --------------------------------------------------------------------------- models
def make_model(name: str, pos_weight: float, seed: int) -> Any:
    if name == "logistic_regression":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            StandardScaler(),
            LogisticRegression(C=0.1, class_weight="balanced", max_iter=3000),
        )
    if name == "random_forest":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            RandomForestClassifier(
                n_estimators=300, min_samples_leaf=5, class_weight="balanced_subsample", n_jobs=-1, random_state=seed
            ),
        )
    if name == "xgboost":
        return XGBClassifier(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            scale_pos_weight=pos_weight,
            eval_metric="aucpr",
            tree_method="hist",
            n_jobs=4,
            random_state=seed,
        )
    raise ValueError(name)


MODEL_NAMES = ("logistic_regression", "random_forest", "xgboost")


def rule_baseline_score(X: pd.DataFrame) -> np.ndarray:
    """Hand-written rule: the largest 24h z-score across sensors (how far any sensor
    has moved from its normal level). Every ML model must beat this."""
    z_cols = [c for c in X.columns if c.endswith("__z_24h")]
    return np.nan_to_num(X[z_cols].abs().max(axis=1).to_numpy(), nan=0.0)


def _pos_weight(y: np.ndarray) -> float:
    pos = max(int(y.sum()), 1)
    return float((len(y) - pos) / pos)


# --------------------------------------------------------------------------- metrics
def ranking_metrics(y: np.ndarray, score: np.ndarray) -> dict[str, float]:
    if y.sum() == 0 or y.sum() == len(y):
        return {
            "pr_auc": float("nan"),
            "roc_auc": float("nan"),
            "best_f1": float("nan"),
            "recall_at_precision_50": float("nan"),
        }
    precision, recall, _ = precision_recall_curve(y, score)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    ok = recall[precision >= 0.5]
    return {
        "pr_auc": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
        "best_f1": float(f1.max()),
        "recall_at_precision_50": float(ok.max()) if len(ok) else 0.0,
    }


def threshold_table(y: np.ndarray, p: np.ndarray, thresholds: dict[str, float]) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for level, thr in thresholds.items():
        pred = p >= thr
        tp = int((pred & (y == 1)).sum())
        fp = int((pred & (y == 0)).sum())
        fn = int((~pred & (y == 1)).sum())
        rows.append(
            {
                "risk_level": level,
                "threshold": thr,
                "precision": tp / (tp + fp) if tp + fp else float("nan"),
                "recall": tp / (tp + fn) if tp + fn else float("nan"),
                "flagged_share": float(pred.mean()),
            }
        )
    return rows


def lead_time_metrics(
    times: pd.DatetimeIndex, groups: np.ndarray, dtf: np.ndarray, p: np.ndarray, threshold: float, horizon_days: int
) -> dict[str, float | int]:
    """For each failure covered by the test rows: was it flagged (p ≥ threshold at
    some observation in the horizon before it), and how many days in advance?"""
    df = pd.DataFrame({"t": times, "g": groups, "dtf": dtf, "p": p})
    df = df[df.dtf <= horizon_days].copy()
    if df.empty:
        return {"failures": 0, "detected": 0, "detection_rate": float("nan"), "median_lead_days": float("nan")}
    df["fail_time"] = df.t + pd.to_timedelta(df.dtf, unit="D")
    df["fail_key"] = df.g.astype(str) + "|" + df.fail_time.dt.round("h").astype(str)
    leads = []
    for _, grp in df.groupby("fail_key"):
        hit = grp[grp.p >= threshold]
        if len(hit):
            leads.append(float(hit.dtf.max()))
    n = int(df.fail_key.nunique())
    return {
        "failures": n,
        "detected": len(leads),
        "detection_rate": len(leads) / n,
        "median_lead_days": float(np.median(leads)) if leads else float("nan"),
    }


def _mean_std(values: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    keys = values[0].keys()
    return {
        k: {"mean": float(np.nanmean([v[k] for v in values])), "std": float(np.nanstd([v[k] for v in values]))}
        for k in keys
    }


# --------------------------------------------------------------------------- calibration
@dataclass
class CalibratedClassifier:
    """Base classifier + isotonic map from raw scores to calibrated probabilities."""

    model: Any
    calibrator: IsotonicRegression

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict_proba(X)[:, 1])

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.clip(self.calibrator.predict(self.raw(X)), 0.0, 1.0)


PRODUCTION_CANDIDATES = ("logistic_regression", "xgboost")


def fit_calibrated(
    algorithm: str, X: pd.DataFrame, y: np.ndarray, groups: np.ndarray, seed: int
) -> CalibratedClassifier:
    """Fit the classifier on ~80% of units, fit isotonic calibration on the other ~20%.

    Calibration matters because class weighting (needed for the rare positive
    class) inflates raw scores; isotonic regression maps them back to observed
    failure frequencies so "p = 0.3" means roughly a 30% chance.
    """
    if algorithm not in PRODUCTION_CANDIDATES:
        raise ValueError(f"production algorithm must be one of {PRODUCTION_CANDIDATES}")
    split = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    fit_idx, cal_idx = next(split.split(X, y, groups))
    model = make_model(algorithm, _pos_weight(y[fit_idx]), seed)
    model.fit(X.iloc[fit_idx], y[fit_idx])
    cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    cal.fit(model.predict_proba(X.iloc[cal_idx])[:, 1], y[cal_idx])
    return CalibratedClassifier(model, cal)


def choose_algorithm(report: dict[str, Any]) -> str:
    """Pick the production algorithm with the best mean cross-validated PR-AUC."""
    cv = report["cross_validation"]
    scored = [(cv[a]["pr_auc"]["mean"], a) for a in PRODUCTION_CANDIDATES if a in cv]
    return max(scored)[1]


# --------------------------------------------------------------------------- RUL & anomaly
def _rul_mask(ds: TrainingSet) -> np.ndarray:
    return np.isfinite(ds.days_to_failure) & (ds.days_to_failure <= RUL_CAP_DAYS)


def make_rul_model(seed: int) -> XGBRegressor:
    return XGBRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:absoluteerror",
        tree_method="hist",
        n_jobs=4,
        random_state=seed,
    )


@dataclass
class AnomalyDetector:
    forest: IsolationForest
    reference: np.ndarray  # sorted raw scores of healthy training rows
    features: tuple[str, ...]

    def score(self, X: pd.DataFrame) -> np.ndarray:
        """0..1: share of healthy training observations that look *less* unusual."""
        raw = -self.forest.score_samples(X[list(self.features)].fillna(0.0).to_numpy())
        return np.searchsorted(self.reference, raw, side="right") / len(self.reference)


def fit_anomaly_detector(ds: TrainingSet, seed: int) -> AnomalyDetector:
    healthy = ~(np.isfinite(ds.days_to_failure) & (ds.days_to_failure <= 30))
    Xh = ds.X.loc[healthy, list(ANOMALY_FEATURES)].fillna(0.0).to_numpy()
    forest = IsolationForest(n_estimators=200, max_samples=min(4096, len(Xh)), random_state=seed, n_jobs=-1)
    forest.fit(Xh)
    ref = np.sort(-forest.score_samples(Xh))
    return AnomalyDetector(forest, ref, ANOMALY_FEATURES)


# --------------------------------------------------------------------------- evaluation
@dataclass
class EvaluationResult:
    metrics: dict[str, Any] = field(default_factory=dict)


def time_split(ds: TrainingSet, quantile: float = 0.75) -> tuple[np.ndarray, np.ndarray, pd.Timestamp]:
    cutoff = ds.times.sort_values()[int(len(ds.times) * quantile)]
    purge = pd.Timedelta(days=ds.horizon_days)
    train = np.asarray(ds.times <= cutoff - purge)
    test = np.asarray(ds.times > cutoff)
    return train, test, cutoff


def evaluate(
    ds: TrainingSet,
    *,
    seed: int = 42,
    models: tuple[str, ...] = MODEL_NAMES,
    algorithm: str = "auto",
    n_folds: int = 5,
) -> dict[str, Any]:
    """Run grouped CV and the time holdout. Returns a JSON-serialisable report.

    ``algorithm="auto"`` picks the production algorithm (logistic regression or
    XGBoost) by cross-validated PR-AUC; the calibrated holdout section then
    evaluates exactly that production configuration.
    """
    t0 = time.perf_counter()
    names = models
    X, y, g = ds.X, ds.y, ds.groups
    report: dict[str, Any] = {
        "dataset": ds.summary(),
        "protocol": {
            "cv": f"GroupKFold({n_folds}) by equipment unit",
            "holdout": "time-ordered: train before cutoff minus horizon, test after cutoff",
            "seed": seed,
        },
    }

    # 1) grouped CV
    cv: dict[str, list[dict[str, float]]] = {n: [] for n in ("rule_baseline", *names)}
    for tr, te in GroupKFold(n_splits=n_folds).split(X, y, g):
        cv["rule_baseline"].append(ranking_metrics(y[te], rule_baseline_score(X.iloc[te])))
        for name in names:
            m = make_model(name, _pos_weight(y[tr]), seed)
            m.fit(X.iloc[tr], y[tr])
            cv[name].append(ranking_metrics(y[te], m.predict_proba(X.iloc[te])[:, 1]))
    report["cross_validation"] = {name: _mean_std(v) for name, v in cv.items()}
    chosen = choose_algorithm(report) if algorithm == "auto" else algorithm
    report["production_algorithm"] = chosen

    # 2) time holdout
    tr, te, cutoff = time_split(ds)
    holdout: dict[str, Any] = {
        "cutoff": cutoff.isoformat(),
        "train_rows": int(tr.sum()),
        "test_rows": int(te.sum()),
        "test_positives": int(y[te].sum()),
        "models": {"rule_baseline": ranking_metrics(y[te], rule_baseline_score(X[te]))},
    }
    for name in names:
        m = make_model(name, _pos_weight(y[tr]), seed)
        m.fit(X[tr], y[tr])
        holdout["models"][name] = ranking_metrics(y[te], m.predict_proba(X[te])[:, 1])

    # Production configuration (calibrated chosen algorithm) on the holdout.
    clf = fit_calibrated(chosen, X[tr], y[tr], g[tr], seed)
    p = clf.predict_proba(X[te])
    holdout["production_calibrated"] = {
        "algorithm": chosen,
        **ranking_metrics(y[te], p),
        "brier": float(brier_score_loss(y[te], p)),
        "brier_base_rate": float(brier_score_loss(y[te], np.full(te.sum(), y[tr].mean()))),
        "reliability": reliability_table(y[te], p),
        "thresholds": threshold_table(y[te], p, DEFAULT_THRESHOLDS),
        "lead_time_at_high": lead_time_metrics(
            ds.times[te], g[te], ds.days_to_failure[te], p, DEFAULT_THRESHOLDS["high"], ds.horizon_days
        ),
    }
    holdout["global_importance"] = global_importance(clf.model, X[te])
    report["holdout"] = holdout

    # 3) RUL and anomaly detection
    report["rul"] = evaluate_rul(ds, tr, te, seed)
    det = fit_anomaly_detector(_subset(ds, tr), seed)
    a = det.score(X[te])
    report["anomaly"] = {"roc_auc_vs_failure_within_horizon": ranking_metrics(y[te], a)["roc_auc"]}
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    report["library_versions"] = {"xgboost": xgboost.__version__, "scikit-learn": sklearn.__version__}
    return report


def _subset(ds: TrainingSet, mask: np.ndarray) -> TrainingSet:
    return TrainingSet(
        X=ds.X[mask].reset_index(drop=True),
        y=ds.y[mask],
        groups=ds.groups[mask],
        times=ds.times[mask],
        days_to_failure=ds.days_to_failure[mask],
        data_end=ds.data_end,
        horizon_days=ds.horizon_days,
    )


def evaluate_rul(ds: TrainingSet, tr: np.ndarray, te: np.ndarray, seed: int) -> dict[str, float | int]:
    mask = _rul_mask(ds)
    trm, tem = tr & mask, te & mask
    if trm.sum() < 50 or tem.sum() < 10:
        return {"note": "not enough failure history to evaluate RUL"}  # type: ignore[dict-item]
    model = make_rul_model(seed)
    model.fit(ds.X[trm], ds.days_to_failure[trm])
    pred = np.clip(model.predict(ds.X[tem]), 0, RUL_CAP_DAYS)
    truth = ds.days_to_failure[tem]
    baseline = np.full_like(truth, np.median(ds.days_to_failure[trm]))
    near = truth <= 14
    return {
        "test_rows": int(tem.sum()),
        "mae_days": float(mean_absolute_error(truth, pred)),
        "rmse_days": float(root_mean_squared_error(truth, pred)),
        "mae_days_last_14d": float(mean_absolute_error(truth[near], pred[near])) if near.any() else float("nan"),
        "baseline_median_mae_days": float(mean_absolute_error(truth, baseline)),
    }


def reliability_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.sum():
            out.append(
                {
                    "bin_low": float(edges[b]),
                    "bin_high": float(edges[b + 1]),
                    "count": int(m.sum()),
                    "mean_predicted": float(p[m].mean()),
                    "observed_rate": float(y[m].mean()),
                }
            )
    return out


def global_importance(model: Any, X: pd.DataFrame, top: int = 15) -> list[dict[str, float | str]]:
    contribs = contributions(model, X)
    imp = np.abs(contribs).mean(axis=0)
    order = np.argsort(imp)[::-1][:top]
    return [{"feature": X.columns[i], "mean_abs_shap": float(imp[i])} for i in order]


def contributions(model: Any, X: pd.DataFrame) -> np.ndarray:
    """Exact SHAP values in log-odds space, shape (n_rows, n_features).

    * XGBoost: TreeSHAP computed by XGBoost itself (``pred_contribs=True``) — the
      same algorithm as ``shap.TreeExplainer``; ``tests/test_ml.py`` checks they agree.
    * Logistic regression (imputer → scaler → LR): for a linear model with
      standardised inputs the SHAP value of feature i is ``coef_i * z_i``, where
      ``z_i`` is the standardised value (its training mean is 0).
    """
    if isinstance(model, XGBClassifier):
        dm = xgboost.DMatrix(X, feature_names=list(X.columns))
        return np.asarray(model.get_booster().predict(dm, pred_contribs=True))[:, :-1]
    if isinstance(model, Pipeline) and isinstance(model[-1], LogisticRegression):
        z = model[:-1].transform(X)
        return np.asarray(z * model[-1].coef_[0])
    raise TypeError(f"no SHAP implementation for {type(model).__name__}")


# --------------------------------------------------------------------------- production bundle
@dataclass
class ModelBundle:
    algorithm: str
    classifier: CalibratedClassifier
    rul_model: XGBRegressor | None
    anomaly: AnomalyDetector
    feature_names: tuple[str, ...]
    horizon_days: int
    trained_on: dict[str, Any]
    library_versions: dict[str, str]


def train_bundle(ds: TrainingSet, *, algorithm: str, seed: int = 42) -> ModelBundle:
    """Train the production models on all labelled data."""
    clf = fit_calibrated(algorithm, ds.X, ds.y, ds.groups, seed)
    mask = _rul_mask(ds)
    rul = None
    if mask.sum() >= 50:
        rul = make_rul_model(seed)
        rul.fit(ds.X[mask], ds.days_to_failure[mask])
    return ModelBundle(
        algorithm=algorithm,
        classifier=clf,
        rul_model=rul,
        anomaly=fit_anomaly_detector(ds, seed),
        feature_names=tuple(ds.X.columns),
        horizon_days=ds.horizon_days,
        trained_on=ds.summary(),
        library_versions={"xgboost": xgboost.__version__, "scikit-learn": sklearn.__version__},
    )
