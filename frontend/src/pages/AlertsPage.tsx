import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Alert, Page } from "../api/types";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, RiskBadge, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

export function AlertsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [status, setStatus] = useState("open");
  const alerts = useQuery({
    queryKey: ["alerts", status],
    queryFn: () => api<Page<Alert>>(`/alerts?limit=200${status ? `&status=${status}` : ""}`),
    refetchInterval: 60_000,
  });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "acknowledge" | "resolve" }) => api(`/alerts/${id}/${action}`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["alerts"] }); void qc.invalidateQueries({ queryKey: ["summary"] }); },
  });
  return (
    <>
      <PageHeader title="Alerts" subtitle="Raised automatically when an equipment's risk level increases to medium or above."
        actions={<Select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)} className="w-44">
          <option value="open">Open</option><option value="acknowledged">Acknowledged</option>
          <option value="resolved">Resolved</option><option value="">All</option>
        </Select>} />
      <ErrorBox error={act.error} />
      <Card>
        {alerts.isLoading ? <Loading /> : alerts.error ? <ErrorBox error={alerts.error} /> : alerts.data!.items.length === 0 ? (
          <Empty>No {status || ""} alerts.</Empty>
        ) : (
          <Table>
            <thead><tr><th>Raised</th><th>Equipment</th><th>Risk</th><th>Message</th><th>Status</th>{can("engineer") && <th>Actions</th>}</tr></thead>
            <tbody>
              {alerts.data!.items.map((a) => (
                <tr key={a.id}>
                  <td className="whitespace-nowrap">{dateTime(a.created_at)}</td>
                  <td><Link className="text-sky-700 hover:underline" to={`/equipment/${a.equipment_id}`}>{a.equipment_name}</Link></td>
                  <td><RiskBadge risk={a.new_risk} /></td>
                  <td className="max-w-md text-slate-700">{a.message}</td>
                  <td className="capitalize">{a.status}</td>
                  {can("engineer") && (
                    <td className="whitespace-nowrap">
                      {a.status === "open" && (
                        <Button variant="secondary" onClick={() => act.mutate({ id: a.id, action: "acknowledge" })}>Acknowledge</Button>
                      )}{" "}
                      {a.status !== "resolved" && <Button variant="secondary" onClick={() => act.mutate({ id: a.id, action: "resolve" })}>Resolve</Button>}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
