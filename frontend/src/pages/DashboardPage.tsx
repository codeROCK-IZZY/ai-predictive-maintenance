import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { CostForecast, Equipment, Page, Summary } from "../api/types";
import { Card, Empty, ErrorBox, HealthBar, Loading, PageHeader, RiskBadge, Stat, Table } from "../components/ui";
import { dateTime, humanize, money, pct, RISK_ORDER, RISK_STYLE } from "../lib/format";

export function DashboardPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api<Summary>("/dashboard/summary") });
  const atRisk = useQuery({
    queryKey: ["equipment", "at-risk"],
    queryFn: async () => {
      const pages = await Promise.all(
        (["critical", "high", "medium"] as const).map((r) => api<Page<Equipment>>(`/equipment?risk=${r}&limit=20`)),
      );
      return pages.flatMap((p) => p.items);
    },
  });
  const cost = useQuery({ queryKey: ["cost"], queryFn: () => api<CostForecast>("/analytics/cost-forecast"), retry: false });

  if (summary.isLoading) return <Loading />;
  if (summary.error) return <ErrorBox error={summary.error} />;
  const s = summary.data!;
  const riskData = RISK_ORDER.map((r) => ({ risk: r, count: s.by_risk[r] ?? 0 }));

  return (
    <>
      <PageHeader title="Fleet overview"
        subtitle={s.active_model
          ? `Active model v${s.active_model.version} (${humanize(s.active_model.algorithm)}), trained ${dateTime(s.active_model.trained_at)}`
          : "No active model yet — an admin must train one on the Models page."} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Equipment" value={s.equipment_total} sub={s.unscored ? `${s.unscored} not yet scored` : "all scored"} />
        <Stat label="Need attention" value={(s.by_risk.high ?? 0) + (s.by_risk.critical ?? 0)} sub="high or critical risk" />
        <Stat label="Open alerts" value={s.open_alerts} sub={<Link className="text-sky-700 hover:underline" to="/alerts">view alerts</Link>} />
        <Stat label="Average health" value={s.average_health ?? "—"} sub={`${s.failures_last_30d} failures in the last 30 days`} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Risk distribution">
          <div className="h-52">
            <ResponsiveContainer>
              <BarChart data={riskData} margin={{ left: -20 }}>
                <XAxis dataKey="risk" tick={{ fontSize: 12 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {riskData.map((d) => <Cell key={d.risk} fill={RISK_STYLE[d.risk].hex} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Equipment needing attention" className="lg:col-span-2">
          {atRisk.isLoading ? <Loading /> : atRisk.data?.length ? (
            <Table>
              <thead><tr><th>Equipment</th><th>Risk</th><th>Health</th><th>Failure prob.</th><th>Est. RUL</th></tr></thead>
              <tbody>
                {atRisk.data.map((e) => (
                  <tr key={e.id}>
                    <td><Link to={`/equipment/${e.id}`} className="font-medium text-sky-700 hover:underline">{e.name}</Link>
                      <div className="text-xs text-slate-500">{e.serial_number} · {e.location}</div></td>
                    <td><RiskBadge risk={e.latest_prediction?.risk_level} /></td>
                    <td><HealthBar score={e.latest_prediction?.health_score} /></td>
                    <td className="tabular-nums">{pct(e.latest_prediction?.failure_probability)}</td>
                    <td className="tabular-nums">{e.latest_prediction?.rul_days != null ? `${e.latest_prediction.rul_days} d` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : <Empty>No equipment at medium risk or above.</Empty>}
        </Card>
      </div>

      {cost.data && (
        <Card title="Maintenance cost outlook" className="mt-6">
          <p className="mb-3 text-sm text-slate-600">
            Expected failure cost over the next {cost.data.horizon_days} days:{" "}
            <strong>{money(cost.data.total_expected_failure_cost)}</strong>. Acting early on the flagged units is estimated at{" "}
            <strong>{money(cost.data.total_preventive_cost_if_acting_on_flagged)}</strong>.
            <span className="block text-xs text-slate-500">Method: {cost.data.method}. Deterministic estimate, not an ML prediction.</span>
          </p>
          <Table>
            <thead><tr><th>Equipment</th><th>Failure prob.</th><th>Expected failure cost</th><th>Planned repair cost</th><th>Suggestion</th></tr></thead>
            <tbody>
              {cost.data.rows.slice(0, 6).map((r) => (
                <tr key={r.equipment_id}>
                  <td><Link className="text-sky-700 hover:underline" to={`/equipment/${r.equipment_id}`}>{r.name}</Link></td>
                  <td className="tabular-nums">{pct(r.failure_probability, 1)}</td>
                  <td className="tabular-nums">{money(r.expected_failure_cost)}</td>
                  <td className="tabular-nums">{money(r.preventive_cost)}</td>
                  <td className={r.recommendation === "act now" ? "font-medium text-red-700" : "text-slate-600"}>{r.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
