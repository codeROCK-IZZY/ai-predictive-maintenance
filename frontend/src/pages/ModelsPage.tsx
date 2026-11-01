import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api } from "../api/client";
import type { Job, ModelVersion } from "../api/types";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, humanize, num, pct } from "../lib/format";

export function ModelsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: () => api<ModelVersion[]>("/models") });
  const [selected, setSelected] = useState<number | null>(null);
  const [algorithm, setAlgorithm] = useState("auto");
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/models/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed"].includes(q.state.data.status) ? false : 3000),
  });
  useEffect(() => {
    if (job.data?.status === "succeeded") {
      setJobId(null);
      void qc.invalidateQueries({ queryKey: ["models"] });
    }
  }, [job.data?.status, qc]);
  const train = useMutation({
    mutationFn: () => api<Job>("/models/train", { body: { algorithm, compare: true, activate: true } }),
    onSuccess: (j) => setJobId(j.id),
  });
  const activate = useMutation({
    mutationFn: (id: number) => api(`/models/${id}/activate`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries(),
  });

  if (models.isLoading) return <Loading />;
  if (models.error) return <ErrorBox error={models.error} />;
  const list = models.data!;
  const current = list.find((m) => m.id === selected) ?? list.find((m) => m.is_active) ?? list[0];

  return (
    <>
      <PageHeader title="Models" subtitle="Every number on this page was produced by the evaluation code at training time."
        actions={can("admin") && <>
          <Select aria-label="Algorithm" value={algorithm} onChange={(e) => setAlgorithm(e.target.value)} className="w-56">
            <option value="auto">Auto (best cross-validated)</option>
            <option value="logistic_regression">Logistic regression</option>
            <option value="xgboost">XGBoost</option>
          </Select>
          <Button onClick={() => train.mutate()} busy={train.isPending || jobId !== null}>
            {jobId !== null ? `Training… (${job.data?.status ?? "queued"})` : "Train new model"}
          </Button>
        </>} />
      <ErrorBox error={train.error ?? activate.error} />
      {job.data?.status === "failed" && <ErrorBox error={new Error(`Training failed: ${job.data.last_error?.split("\n")[0]}`)} />}
      {list.length === 0 ? <Card><Empty>No models trained yet.</Empty></Card> : (
        <>
          <Card title="Versions">
            <Table>
              <thead><tr><th>Version</th><th>Algorithm</th><th>Trained</th><th>Holdout PR-AUC</th><th>Holdout ROC-AUC</th><th>Brier</th><th>Status</th><th /></tr></thead>
              <tbody>
                {list.map((m) => {
                  const p = m.metrics.holdout.production_calibrated;
                  return (
                    <tr key={m.id} className={m.id === current?.id ? "bg-sky-50" : ""}>
                      <td><button className="font-medium text-sky-700 hover:underline" onClick={() => setSelected(m.id)}>v{m.version}</button></td>
                      <td>{humanize(m.algorithm)}</td>
                      <td>{dateTime(m.trained_at)}</td>
                      <td className="tabular-nums">{num(p.pr_auc, 3)}</td>
                      <td className="tabular-nums">{num(p.roc_auc, 3)}</td>
                      <td className="tabular-nums">{num(p.brier, 4)}</td>
                      <td>{m.is_active ? <span className="font-medium text-emerald-700">active</span> : "inactive"}</td>
                      <td>{can("admin") && !m.is_active && (
                        <Button variant="secondary" busy={activate.isPending} onClick={() => activate.mutate(m.id)}>Activate</Button>
                      )}</td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Card>
          {current && <ModelDetail m={current} />}
        </>
      )}
    </>
  );
}

function ModelDetail({ m }: { m: ModelVersion }) {
  const { cross_validation: cv, holdout: ho, dataset: ds } = m.metrics;
  const prod = ho.production_calibrated;
  const lt = prod.lead_time_at_high;
  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-2">
      <Card title={`v${m.version}: model comparison`} className="lg:col-span-2">
        <p className="mb-3 text-xs text-slate-600">
          {ds.rows.toLocaleString()} observations from {ds.units} units, {pct(ds.positive_rate, 1)} positive (failure within {m.horizon_days} days).
          CV = grouped by unit (5 folds); holdout = later time period (after {dateTime(ho.cutoff)}).
          A random model scores PR-AUC ≈ {pct(ho.test_positives / Math.max(ho.test_rows, 1), 1)}.
        </p>
        <Table>
          <thead><tr><th>Model</th><th>CV PR-AUC</th><th>CV ROC-AUC</th><th>Holdout PR-AUC</th><th>Holdout recall @ precision ≥ 0.5</th></tr></thead>
          <tbody>
            {Object.entries(cv).map(([name, s]) => (
              <tr key={name} className={name === m.metrics.production_algorithm ? "font-medium" : ""}>
                <td>{humanize(name)}{name === m.metrics.production_algorithm && " (selected)"}</td>
                <td className="tabular-nums">{num(s.pr_auc?.mean, 3)} ± {num(s.pr_auc?.std, 3)}</td>
                <td className="tabular-nums">{num(s.roc_auc?.mean, 3)} ± {num(s.roc_auc?.std, 3)}</td>
                <td className="tabular-nums">{num(ho.models[name]?.pr_auc ?? null, 3)}</td>
                <td className="tabular-nums">{num(ho.models[name]?.recall_at_precision_50 ?? null, 3)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <Card title="Risk thresholds on the holdout">
        <Table>
          <thead><tr><th>Level</th><th>Threshold</th><th>Precision</th><th>Recall</th><th>Flagged</th></tr></thead>
          <tbody>
            {prod.thresholds.map((t) => (
              <tr key={t.risk_level}><td className="capitalize">{t.risk_level}</td><td>≥ {t.threshold}</td>
                <td className="tabular-nums">{num(t.precision, 3)}</td><td className="tabular-nums">{num(t.recall, 3)}</td>
                <td className="tabular-nums">{pct(t.flagged_share, 1)}</td></tr>
            ))}
          </tbody>
        </Table>
        <p className="mt-3 text-sm text-slate-700">
          At the <em>high</em> threshold, <strong>{lt.detected} of {lt.failures}</strong> holdout failures were flagged in the
          {" "}{m.horizon_days} days before they occurred; median warning <strong>{num(lt.median_lead_days, 1)} days</strong>.
        </p>
        <p className="mt-2 text-xs text-slate-500">
          RUL: MAE {num(m.metrics.rul.mae_days ?? null, 1)} days (baseline {num(m.metrics.rul.baseline_median_mae_days ?? null, 1)}).
          Anomaly-score ROC-AUC: {num(m.metrics.anomaly.roc_auc_vs_failure_within_horizon, 3)}.
        </p>
      </Card>
      <Card title="Calibration (holdout)">
        <div className="h-56">
          <ResponsiveContainer>
            <ScatterChart margin={{ left: -10, right: 10 }}>
              <CartesianGrid stroke="#f1f5f9" />
              <XAxis type="number" dataKey="mean_predicted" domain={[0, 1]} name="predicted" tick={{ fontSize: 11 }}
                label={{ value: "predicted probability", position: "insideBottom", offset: -2, fontSize: 11 }} />
              <YAxis type="number" dataKey="observed_rate" domain={[0, 1]} name="observed" tick={{ fontSize: 11 }} />
              <ZAxis dataKey="count" range={[30, 300]} name="observations" />
              <Tooltip formatter={(v: number) => v.toFixed(3)} />
              <Scatter data={prod.reliability} fill="#0369a1" />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        <p className="text-xs text-slate-500">Points on the diagonal = well calibrated. Brier {num(prod.brier, 4)} vs. {num(prod.brier_base_rate, 4)} for always predicting the base rate.</p>
      </Card>
      <Card title="Most influential signals (mean |SHAP|)" className="lg:col-span-2">
        <div className="h-96">
          <ResponsiveContainer>
            <BarChart data={ho.global_importance} layout="vertical" margin={{ left: 40, right: 16 }}>
              <CartesianGrid stroke="#f1f5f9" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="feature" tick={{ fontSize: 11 }} width={220} interval={0} />
              <Tooltip formatter={(v: number) => v.toFixed(3)} />
              <Bar dataKey="mean_abs_shap" fill="#0369a1" radius={[0, 3, 3, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}
