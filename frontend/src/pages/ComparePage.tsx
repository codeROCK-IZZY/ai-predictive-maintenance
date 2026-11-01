import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ComparisonRow } from "../api/types";
import { Card, ErrorBox, HealthBar, Loading, PageHeader, RiskBadge, Table } from "../components/ui";
import { money, num, pct } from "../lib/format";

export function ComparePage() {
  const [params] = useSearchParams();
  const ids = params.getAll("ids");
  const q = useQuery({
    queryKey: ["compare", ids],
    queryFn: () => api<ComparisonRow[]>(`/analytics/compare?${ids.map((i) => `ids=${i}`).join("&")}`),
    enabled: ids.length >= 2,
  });
  if (ids.length < 2) return <p className="text-sm">Select at least two units on the Equipment page.</p>;
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const rows = q.data!;
  const metrics: [string, (r: ComparisonRow) => React.ReactNode][] = [
    ["Type", (r) => r.type],
    ["Age", (r) => (r.age_years != null ? `${r.age_years} years` : "—")],
    ["Risk", (r) => <RiskBadge risk={r.risk_level} />],
    ["Health", (r) => <HealthBar score={r.health_score} />],
    ["Failure probability", (r) => pct(r.failure_probability, 1)],
    ["Estimated RUL", (r) => (r.rul_days != null ? `${r.rul_days} days` : "—")],
    ["Failures (last year)", (r) => r.failures_last_year],
    ["MTBF", (r) => (r.mtbf_days != null ? `${num(r.mtbf_days, 0)} days` : "no failures")],
    ["Maintenance cost (last year)", (r) => money(r.maintenance_cost_last_year)],
    ["Days since service", (r) => num(r.days_since_service, 0)],
  ];
  return (
    <>
      <PageHeader title="Equipment comparison" subtitle="MTBF = days in the last year ÷ failures in the last year" />
      <Card>
        <Table>
          <thead>
            <tr><th>Metric</th>{rows.map((r) => (
              <th key={r.equipment_id}><Link className="normal-case text-sky-700 hover:underline" to={`/equipment/${r.equipment_id}`}>{r.name}</Link></th>
            ))}</tr>
          </thead>
          <tbody>
            {metrics.map(([label, fn]) => (
              <tr key={label}><td className="font-medium text-slate-600">{label}</td>
                {rows.map((r) => <td key={r.equipment_id} className="tabular-nums">{fn(r)}</td>)}</tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </>
  );
}
