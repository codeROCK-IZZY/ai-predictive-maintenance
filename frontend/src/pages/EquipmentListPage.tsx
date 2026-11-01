import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, download } from "../api/client";
import type { Equipment, EquipmentType, Page } from "../api/types";
import { Button, Card, Empty, ErrorBox, Field, HealthBar, Input, Loading, PageHeader, RiskBadge, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { pct } from "../lib/format";

const PAGE = 25;

export function EquipmentListPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [type, setType] = useState("");
  const [risk, setRisk] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [showForm, setShowForm] = useState(false);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (type) params.set("type", type);
  if (risk) params.set("risk", risk);
  if (q) params.set("q", q);
  const list = useQuery({ queryKey: ["equipment", params.toString()], queryFn: () => api<Page<Equipment>>(`/equipment?${params}`) });
  const types = useQuery({ queryKey: ["types"], queryFn: () => api<EquipmentType[]>("/equipment-types") });

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length < 10 ? [...s, id] : s));

  return (
    <>
      <PageHeader title="Equipment" subtitle={list.data ? `${list.data.total} units` : undefined}
        actions={<>
          <Button variant="secondary" disabled={selected.length < 2}
            onClick={() => navigate(`/equipment/compare?${selected.map((s) => `ids=${s}`).join("&")}`)}>
            Compare ({selected.length})
          </Button>
          <Button variant="secondary" onClick={() => void download("/reports/fleet.csv", "fleet-status.csv")}>Export CSV</Button>
          {can("engineer") && <Button onClick={() => setShowForm((v) => !v)}>{showForm ? "Close" : "Add equipment"}</Button>}
        </>} />

      {showForm && <AddEquipment types={types.data ?? []} onDone={() => setShowForm(false)} />}

      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <Input placeholder="Search name, serial, location…" aria-label="Search" value={q}
            onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
          <Select aria-label="Equipment type" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
            <option value="">All types</option>
            {types.data?.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
          </Select>
          <Select aria-label="Risk level" value={risk} onChange={(e) => { setRisk(e.target.value); setOffset(0); }}>
            <option value="">All risk levels</option>
            {["critical", "high", "medium", "low"].map((r) => <option key={r} value={r}>{r}</option>)}
          </Select>
        </div>
        {list.isLoading ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : list.data!.items.length === 0 ? (
          <Empty>No equipment matches these filters.</Empty>
        ) : (
          <>
            <Table>
              <thead>
                <tr><th><span className="sr-only">Select</span></th><th>Name</th><th>Type</th><th>Location</th><th>Status</th>
                  <th>Risk</th><th>Health</th><th>Failure prob.</th></tr>
              </thead>
              <tbody>
                {list.data!.items.map((e) => (
                  <tr key={e.id} className="hover:bg-slate-50">
                    <td><input type="checkbox" aria-label={`Select ${e.name} for comparison`} checked={selected.includes(e.id)}
                      onChange={() => toggle(e.id)} /></td>
                    <td><Link to={`/equipment/${e.id}`} className="font-medium text-sky-700 hover:underline">{e.name}</Link>
                      <div className="text-xs text-slate-500">{e.serial_number}</div></td>
                    <td>{e.equipment_type_name}</td>
                    <td className="text-slate-600">{e.location || "—"}</td>
                    <td className="capitalize">{e.status}</td>
                    <td><RiskBadge risk={e.latest_prediction?.risk_level} /></td>
                    <td><HealthBar score={e.latest_prediction?.health_score} /></td>
                    <td className="tabular-nums">{pct(e.latest_prediction?.failure_probability, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
              <span>{offset + 1}–{Math.min(offset + PAGE, list.data!.total)} of {list.data!.total}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Previous</Button>
                <Button variant="secondary" disabled={offset + PAGE >= list.data!.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function AddEquipment({ types, onDone }: { types: EquipmentType[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", serial_number: "", equipment_type_code: "pump", manufacturer: "", model: "",
    install_date: "", location: "" });
  const create = useMutation({
    mutationFn: () => api<Equipment>("/equipment", { body: { ...form, install_date: form.install_date || null } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["equipment"] }); onDone(); },
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  return (
    <Card title="Register equipment" className="mb-6">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-3">
        <Field label="Name"><Input required value={form.name} onChange={set("name")} /></Field>
        <Field label="Serial number"><Input required value={form.serial_number} onChange={set("serial_number")} /></Field>
        <Field label="Type">
          <Select value={form.equipment_type_code} onChange={set("equipment_type_code")}>
            {types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
          </Select>
        </Field>
        <Field label="Manufacturer"><Input value={form.manufacturer} onChange={set("manufacturer")} /></Field>
        <Field label="Model"><Input value={form.model} onChange={set("model")} /></Field>
        <Field label="Install date"><Input type="date" value={form.install_date} onChange={set("install_date")} /></Field>
        <Field label="Location"><Input value={form.location} onChange={set("location")} /></Field>
        <div className="flex items-end gap-2 sm:col-span-2">
          <Button type="submit" busy={create.isPending}>Save</Button>
          <Button type="button" variant="secondary" onClick={onDone}>Cancel</Button>
        </div>
        <div className="sm:col-span-3"><ErrorBox error={create.error} /></div>
      </form>
    </Card>
  );
}
