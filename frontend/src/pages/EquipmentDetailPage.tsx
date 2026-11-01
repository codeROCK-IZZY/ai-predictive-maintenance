import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  Equipment, EquipmentType, FailureEvent, MaintenanceEvent, Prediction, PredictionSummary as PS, Series,
  SimulationResult, Thresholds,
} from "../api/types";
import { RiskHistoryChart, SensorChart } from "../components/Charts";
import { FactorList, PredictionSummary, RecommendationList } from "../components/PredictionPanel";
import { Button, Card, Empty, ErrorBox, Field, Input, Loading, RiskBadge, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { date, dateTime, humanize, money, pct } from "../lib/format";

const RANGES = { "24h": { days: 1, bucket: "10m" }, "7d": { days: 7, bucket: "1h" }, "30d": { days: 30, bucket: "6h" },
  "90d": { days: 90, bucket: "1d" } } as const;
const MODES: Record<string, string[]> = { pump: ["bearing_wear", "seal_leak"], compressor: ["bearing_wear", "seal_leak", "winding_fault"],
  motor: ["bearing_wear", "winding_fault"] };

export function EquipmentDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const eq = useQuery({ queryKey: ["equipment", id], queryFn: () => api<Equipment>(`/equipment/${id}`) });
  const latest = useQuery({
    queryKey: ["prediction", id],
    queryFn: () => api<Prediction>(`/equipment/${id}/predictions/latest`).catch((e) => {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }),
  });
  const predict = useMutation({
    mutationFn: () => api<Prediction>(`/equipment/${id}/predict`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["prediction", id] }); void qc.invalidateQueries({ queryKey: ["history", id] }); },
  });

  if (eq.isLoading) return <Loading />;
  if (eq.error) return <ErrorBox error={eq.error} />;
  const e = eq.data!;

  return (
    <>
      <nav className="mb-2 text-sm"><Link to="/equipment" className="text-sky-700 hover:underline">← Equipment</Link></nav>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{e.name}</h1>
          <p className="text-sm text-slate-500">
            {e.equipment_type_name} · {e.serial_number} · {e.manufacturer} {e.model} · {e.location || "no location"} ·
            installed {date(e.install_date)} · <span className="capitalize">{e.status}</span>
          </p>
        </div>
        {can("engineer") && <Button onClick={() => predict.mutate()} busy={predict.isPending}>Run prediction now</Button>}
      </div>
      <ErrorBox error={predict.error} />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Current condition" className="lg:col-span-3">
          {latest.isLoading ? <Loading /> : latest.data ? <PredictionSummary p={latest.data} /> : (
            <Empty>No prediction yet. {can("engineer") ? "Run a prediction once sensor data and an active model exist." : ""}</Empty>
          )}
        </Card>
        {latest.data && <>
          <Card title="Why this prediction" className="lg:col-span-2"
            actions={<span className="text-xs text-slate-500">SHAP contributions, grouped by sensor</span>}>
            <FactorList factors={latest.data.explanation.top_factors} />
          </Card>
          <Card title="Recommended actions"><RecommendationList recs={latest.data.recommendations} /></Card>
        </>}
        <SensorPanel equipment={e} />
        <HistoryPanel id={id} />
        <MaintenancePanel id={id} />
        <SimulatorPanel equipment={e} />
      </div>
    </>
  );
}

function SensorPanel({ equipment }: { equipment: Equipment }) {
  const [range, setRange] = useState<keyof typeof RANGES>("7d");
  const types = useQuery({ queryKey: ["types"], queryFn: () => api<EquipmentType[]>("/equipment-types") });
  const sensorNames = Object.fromEntries(
    (types.data?.find((t) => t.code === equipment.equipment_type_code)?.sensors ?? []).map((s) => [s.code, s.name]));
  const series = useQuery({
    queryKey: ["readings", equipment.id, range],
    queryFn: async () => {
      // Anchor the window on the latest reading so historical/demo data still shows.
      const probe = await api<Series[]>(`/equipment/${equipment.id}/readings?bucket=1d`);
      const last = probe.flatMap((s) => s.points.map((p) => p.ts)).sort().at(-1);
      const end = last ? new Date(new Date(last).getTime() + 86_400_000) : new Date();
      const start = new Date(end.getTime() - RANGES[range].days * 86_400_000);
      const qs = new URLSearchParams({ from: start.toISOString(), to: end.toISOString(), bucket: RANGES[range].bucket });
      return api<Series[]>(`/equipment/${equipment.id}/readings?${qs}`);
    },
  });
  return (
    <Card title="Sensor trends" className="lg:col-span-3" actions={
      <div role="group" aria-label="Time range" className="flex gap-1">
        {(Object.keys(RANGES) as (keyof typeof RANGES)[]).map((r) => (
          <button key={r} onClick={() => setRange(r)} aria-pressed={range === r}
            className={`rounded px-2 py-0.5 text-xs ${range === r ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-700"}`}>{r}</button>
        ))}
      </div>}>
      {series.isLoading ? <Loading /> : series.error ? <ErrorBox error={series.error} /> :
        series.data!.every((s) => s.points.length === 0) ? <Empty>No sensor data in this range.</Empty> : (
          <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
            {series.data!.map((s) => <SensorChart key={s.sensor} series={s} label={sensorNames[s.sensor] ?? s.sensor} />)}
          </div>
        )}
      <p className="mt-2 text-xs text-slate-500">Line: average per bucket. Shaded band: min–max within the bucket.</p>
    </Card>
  );
}

function HistoryPanel({ id }: { id: string }) {
  const history = useQuery({ queryKey: ["history", id], queryFn: () => api<PS[]>(`/equipment/${id}/predictions?limit=500`) });
  const thresholds = useQuery({ queryKey: ["thresholds"], queryFn: () => api<Thresholds>("/config/risk-thresholds") });
  return (
    <Card title="Failure probability history" className="lg:col-span-3">
      {history.isLoading ? <Loading /> : history.data && history.data.length > 1 ? (
        <RiskHistoryChart history={history.data} thresholds={thresholds.data} />
      ) : <Empty>Not enough predictions yet to show a trend.</Empty>}
    </Card>
  );
}

function MaintenancePanel({ id }: { id: string }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const maint = useQuery({ queryKey: ["maintenance", id], queryFn: () => api<MaintenanceEvent[]>(`/equipment/${id}/maintenance`) });
  const failures = useQuery({ queryKey: ["failures", id], queryFn: () => api<FailureEvent[]>(`/equipment/${id}/failures`) });
  const [form, setForm] = useState({ maintenance_type: "inspection", performed_at: new Date().toISOString().slice(0, 16), cost: "", notes: "" });
  const add = useMutation({
    mutationFn: () => api(`/equipment/${id}/maintenance`, {
      body: { ...form, performed_at: new Date(form.performed_at).toISOString(), cost: form.cost === "" ? null : Number(form.cost) },
    }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["maintenance", id] }); setForm({ ...form, cost: "", notes: "" }); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(); };
  return (
    <Card title="Maintenance & failure history" className="lg:col-span-2">
      {can("engineer") && (
        <form onSubmit={submit} className="mb-4 grid gap-3 rounded-lg bg-slate-50 p-3 sm:grid-cols-4">
          <Field label="Type">
            <Select value={form.maintenance_type} onChange={(e) => setForm({ ...form, maintenance_type: e.target.value })}>
              {["inspection", "preventive", "repair", "replacement"].map((t) => <option key={t}>{t}</option>)}
            </Select>
          </Field>
          <Field label="Performed at"><Input type="datetime-local" required value={form.performed_at}
            onChange={(e) => setForm({ ...form, performed_at: e.target.value })} /></Field>
          <Field label="Cost (USD)"><Input type="number" min="0" step="0.01" value={form.cost}
            onChange={(e) => setForm({ ...form, cost: e.target.value })} /></Field>
          <div className="flex items-end"><Button type="submit" busy={add.isPending} className="w-full">Log maintenance</Button></div>
          <div className="sm:col-span-4"><Field label="Notes"><Input value={form.notes} maxLength={5000}
            onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
          <div className="sm:col-span-4"><ErrorBox error={add.error} /></div>
        </form>
      )}
      {maint.isLoading ? <Loading /> : (
        <Table>
          <thead><tr><th>Date</th><th>Type</th><th>Cost</th><th>Notes</th></tr></thead>
          <tbody>
            {[
              ...(failures.data ?? []).map((f) => ({ id: f.id, at: f.occurred_at, failure: true, type: "failure", cost: null, notes: humanize(f.failure_mode) })),
              ...maint.data!.map((m) => ({ id: m.id, at: m.performed_at, failure: false, type: m.maintenance_type, cost: m.cost, notes: m.notes })),
            ].sort((a, b) => b.at.localeCompare(a.at)).map((r) => (
              <tr key={r.id} className={r.failure ? "bg-red-50/50" : ""}>
                <td className="whitespace-nowrap">{dateTime(r.at)}</td>
                <td className={r.failure ? "font-medium text-red-700" : "capitalize"}>{r.type}</td>
                <td className="tabular-nums">{money(r.cost)}</td>
                <td className="text-slate-600">{r.notes}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function SimulatorPanel({ equipment }: { equipment: Equipment }) {
  const modes = MODES[equipment.equipment_type_code] ?? ["bearing_wear"];
  const [mode, setMode] = useState(modes[0]!);
  const [severity, setSeverity] = useState(1);
  const [days, setDays] = useState(7);
  const sim = useMutation({
    mutationFn: () => api<{ baseline: SimulationResult; simulated: SimulationResult; note: string }>(
      `/equipment/${equipment.id}/simulate`, { body: { failure_mode: mode, severity, ramp_days: days } }),
  });
  return (
    <Card title="What-if failure simulator">
      <p className="mb-3 text-xs text-slate-500">Injects a simulated degradation into this unit's recent sensor data and re-scores it. Nothing is saved.</p>
      <div className="space-y-3">
        <Field label="Failure mode">
          <Select value={mode} onChange={(e) => setMode(e.target.value)}>
            {modes.map((m) => <option key={m} value={m}>{humanize(m)}</option>)}
          </Select>
        </Field>
        <Field label={`Severity: ${severity.toFixed(1)}`}>
          <input type="range" min={0.1} max={1.5} step={0.1} value={severity} onChange={(e) => setSeverity(Number(e.target.value))} className="w-full" />
        </Field>
        <Field label={`Develops over: ${days} days`}>
          <input type="range" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-full" />
        </Field>
        <Button onClick={() => sim.mutate()} busy={sim.isPending} className="w-full">Run simulation</Button>
        <ErrorBox error={sim.error} />
        {sim.data && (
          <div className="rounded-lg border border-slate-200 p-3 text-sm">
            <div className="flex items-center justify-between">
              <span>Now: {pct(sim.data.baseline.failure_probability, 1)} <RiskBadge risk={sim.data.baseline.risk_level} /></span>
              <span aria-hidden>→</span>
              <span>Simulated: <strong>{pct(sim.data.simulated.failure_probability, 1)}</strong> <RiskBadge risk={sim.data.simulated.risk_level} /></span>
            </div>
            <p className="mt-2 text-xs text-slate-600">Top factor: {sim.data.simulated.top_factors[0]?.text}</p>
            <p className="mt-1 text-xs text-slate-600">{sim.data.simulated.recommendations[0]?.action}</p>
          </div>
        )}
      </div>
    </Card>
  );
}
