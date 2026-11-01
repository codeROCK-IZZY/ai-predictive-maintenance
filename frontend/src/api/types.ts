export type Role = "admin" | "engineer" | "viewer";
export type Risk = "low" | "medium" | "high" | "critical";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface Sensor {
  id: number;
  code: string;
  name: string;
  unit: string;
  min_valid: number;
  max_valid: number;
}

export interface EquipmentType {
  id: number;
  code: string;
  name: string;
  description: string;
  sensors: Sensor[];
}

export interface LatestPrediction {
  id: string;
  as_of: string;
  failure_probability: number;
  health_score: number;
  risk_level: Risk;
  rul_days: number | null;
}

export interface Equipment {
  id: string;
  name: string;
  serial_number: string;
  manufacturer: string;
  model: string;
  install_date: string | null;
  location: string;
  operating_conditions: Record<string, unknown>;
  status: "active" | "maintenance" | "retired";
  equipment_type_code: string;
  equipment_type_name: string;
  created_at: string;
  updated_at: string;
  latest_prediction: LatestPrediction | null;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Factor {
  signal: string;
  feature: string;
  value: number | null;
  contribution: number;
  effect: string;
  text: string;
  details?: { feature: string; contribution: number }[];
}

export interface Recommendation {
  source: "rule";
  priority: "low" | "medium" | "high" | "critical";
  suspected_failure_mode: string | null;
  action: string;
}

export interface Prediction {
  id: string;
  equipment_id: string;
  as_of: string;
  failure_probability: number;
  health_score: number;
  risk_level: Risk;
  rul_days: number | null;
  anomaly_score: number | null;
  explanation: {
    method: string;
    algorithm: string;
    top_factors: Factor[];
    suspected_failure_mode: string | null;
    data_quality: { hours_of_history: number; baseline_available: boolean; last_reading_hour: string; stale?: boolean };
    caveat: string;
  };
  recommendations: Recommendation[];
  created_at: string;
  model: { id: number; name: string; version: number; algorithm: string; horizon_days: number } | null;
}

export interface PredictionSummary {
  id: string;
  as_of: string;
  failure_probability: number;
  health_score: number;
  risk_level: Risk;
  rul_days: number | null;
  anomaly_score: number | null;
}

export interface Series {
  sensor: string;
  unit: string;
  bucket: string;
  points: { ts: string; avg: number; min: number; max: number; count: number }[];
}

export interface Alert {
  id: string;
  equipment_id: string;
  equipment_name: string | null;
  previous_risk: Risk | null;
  new_risk: Risk;
  status: "open" | "acknowledged" | "resolved";
  message: string;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface MaintenanceEvent {
  id: string;
  performed_at: string;
  maintenance_type: "inspection" | "repair" | "replacement" | "preventive";
  notes: string;
  cost: string | null;
}

export interface FailureEvent {
  id: string;
  occurred_at: string;
  failure_mode: string;
  description: string;
}

export interface Batch {
  id: string;
  source: "api" | "csv";
  filename: string | null;
  status: "pending" | "processing" | "completed" | "failed";
  rows_total: number;
  rows_accepted: number;
  rows_rejected: number;
  rows_duplicate: number;
  errors: { row: number; error: string }[];
  created_at: string;
  finished_at: string | null;
}

export interface Summary {
  equipment_total: number;
  by_status: Record<string, number>;
  by_risk: Record<Risk, number>;
  unscored: number;
  open_alerts: number;
  failures_last_30d: number;
  average_health: number | null;
  active_model: { id: number; version: number; algorithm: string; trained_at: string } | null;
}

type Stat = { mean: number; std: number };
export interface ModelVersion {
  id: number;
  name: string;
  version: number;
  algorithm: string;
  horizon_days: number;
  is_active: boolean;
  trained_at: string;
  feature_names: string[];
  data_snapshot: Record<string, unknown>;
  metrics: {
    production_algorithm: string;
    dataset: { rows: number; positives: number; positive_rate: number; units: number };
    cross_validation: Record<string, Record<string, Stat>>;
    holdout: {
      cutoff: string;
      test_rows: number;
      test_positives: number;
      models: Record<string, Record<string, number | null>>;
      production_calibrated: {
        algorithm: string;
        pr_auc: number;
        roc_auc: number;
        brier: number;
        brier_base_rate: number;
        thresholds: { risk_level: string; threshold: number; precision: number | null; recall: number | null; flagged_share: number }[];
        reliability: { bin_low: number; bin_high: number; count: number; mean_predicted: number; observed_rate: number }[];
        lead_time_at_high: { failures: number; detected: number; detection_rate: number | null; median_lead_days: number | null };
      };
      global_importance: { feature: string; mean_abs_shap: number }[];
    };
    rul: { mae_days?: number; baseline_median_mae_days?: number; note?: string };
    anomaly: { roc_auc_vs_failure_within_horizon: number | null };
  };
}

export interface Job {
  id: number;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  last_error: string | null;
  result: Record<string, unknown> | null;
}

export interface Thresholds {
  medium: number;
  high: number;
  critical: number;
}

export interface CostForecast {
  horizon_days: number;
  method: string;
  total_expected_failure_cost: number;
  total_preventive_cost_if_acting_on_flagged: number;
  rows: { equipment_id: string; name: string; failure_probability: number; expected_failure_cost: number; preventive_cost: number; recommendation: string }[];
}

export interface ComparisonRow {
  equipment_id: string;
  name: string;
  type: string;
  age_years: number | null;
  risk_level: Risk | null;
  health_score: number | null;
  failure_probability: number | null;
  rul_days: number | null;
  failures_last_year: number;
  mtbf_days: number | null;
  maintenance_cost_last_year: number;
  days_since_service: number | null;
}

export interface SimulationResult {
  failure_probability: number;
  health_score: number;
  risk_level: Risk;
  rul_days: number | null;
  top_factors: Factor[];
  recommendations: Recommendation[];
}
