import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Batch } from "../api/types";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, Table } from "../components/ui";
import { dateTime } from "../lib/format";

export function IngestionPage() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const batches = useQuery({
    queryKey: ["batches"],
    queryFn: () => api<Batch[]>("/ingestion/batches?limit=30"),
    // Poll while any import is still running.
    refetchInterval: (q) => (q.state.data?.some((b) => b.status === "pending" || b.status === "processing") ? 2000 : false),
  });
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      return api<Batch>("/ingestion/csv", { form });
    },
    onSuccess: () => { setFile(null); void qc.invalidateQueries({ queryKey: ["batches"] }); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (file) upload.mutate(); };

  return (
    <>
      <PageHeader title="Data ingestion" subtitle="Upload sensor readings as CSV, or send them to the REST API." />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Upload CSV" className="lg:col-span-2">
          <form onSubmit={submit} className="space-y-3">
            <input type="file" accept=".csv,text/csv" aria-label="CSV file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm" />
            <Button type="submit" disabled={!file} busy={upload.isPending}>Upload</Button>
            <ErrorBox error={upload.error} />
          </form>
        </Card>
        <Card title="File format">
          <pre className="overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">{`equipment_serial,sensor,ts,value
PUM-1000,vibration_rms,2026-06-01T00:00:00Z,2.31
PUM-1000,flow_rate,2026-06-01T00:00:00Z,118.4`}</pre>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-600">
            <li>Timestamps must include a timezone (e.g. <code>Z</code>).</li>
            <li>Sensor codes must exist for the equipment's type; values must be within the sensor's valid range.</li>
            <li>Invalid rows are rejected and reported; valid rows are stored. Duplicates are skipped.</li>
            <li>Maximum file size: 50 MB.</li>
          </ul>
        </Card>
      </div>
      <Card title="Recent imports" className="mt-6">
        {batches.isLoading ? <Loading /> : !batches.data?.length ? <Empty>No imports yet.</Empty> : (
          <Table>
            <thead><tr><th>Started</th><th>Source</th><th>File</th><th>Status</th><th>Total</th><th>Accepted</th><th>Rejected</th><th>Duplicates</th></tr></thead>
            <tbody>
              {batches.data.map((b) => (
                <tr key={b.id} className="align-top">
                  <td className="whitespace-nowrap">{dateTime(b.created_at)}</td>
                  <td className="uppercase">{b.source}</td>
                  <td>{b.filename ?? "—"}
                    {b.errors.length > 0 && (
                      <details className="mt-1 text-xs text-red-700"><summary className="cursor-pointer">{b.errors.length} error(s)</summary>
                        <ul className="mt-1 space-y-0.5">{b.errors.slice(0, 20).map((e, i) => <li key={i}>Row {e.row}: {e.error}</li>)}</ul>
                      </details>
                    )}
                  </td>
                  <td className="capitalize">{b.status}</td>
                  <td className="tabular-nums">{b.rows_total}</td>
                  <td className="tabular-nums text-emerald-700">{b.rows_accepted}</td>
                  <td className="tabular-nums text-red-700">{b.rows_rejected}</td>
                  <td className="tabular-nums">{b.rows_duplicate}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
