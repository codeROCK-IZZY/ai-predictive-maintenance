import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Role, Thresholds, User } from "../api/types";
import { Button, Card, ErrorBox, Field, Input, Loading, PageHeader, Select, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

export function AdminPage() {
  return (
    <>
      <PageHeader title="Administration" />
      <div className="grid gap-6 lg:grid-cols-3">
        <UsersCard />
        <ThresholdsCard />
      </div>
    </>
  );
}

function UsersCard() {
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "viewer" as Role });
  const create = useMutation({
    mutationFn: () => api<User>("/users", { body: form }),
    onSuccess: () => { setForm({ email: "", full_name: "", password: "", role: "viewer" }); void qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<User> }) => api(`/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  return (
    <Card title="Users" className="lg:col-span-2">
      <form onSubmit={submit} className="mb-4 grid gap-3 rounded-lg bg-slate-50 p-3 sm:grid-cols-4">
        <Field label="Email"><Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Name"><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <Field label="Initial password" hint="At least 12 characters">
          <Input type="password" required minLength={12} autoComplete="new-password" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Role">
          <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            <option value="viewer">viewer</option><option value="engineer">engineer</option><option value="admin">admin</option>
          </Select>
        </Field>
        <div className="sm:col-span-4"><Button type="submit" busy={create.isPending}>Create user</Button></div>
        <div className="sm:col-span-4"><ErrorBox error={create.error ?? update.error} /></div>
      </form>
      {users.isLoading ? <Loading /> : (
        <Table>
          <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Created</th><th /></tr></thead>
          <tbody>
            {users.data!.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td><td>{u.full_name}</td>
                <td>
                  <Select aria-label={`Role for ${u.email}`} value={u.role} disabled={u.id === me?.id} className="w-32"
                    onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                    <option value="viewer">viewer</option><option value="engineer">engineer</option><option value="admin">admin</option>
                  </Select>
                </td>
                <td>{u.is_active ? "active" : <span className="text-slate-400">deactivated</span>}</td>
                <td>{dateTime(u.created_at)}</td>
                <td>{u.id !== me?.id && (
                  <Button variant="secondary" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                    {u.is_active ? "Deactivate" : "Reactivate"}
                  </Button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function ThresholdsCard() {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["thresholds"], queryFn: () => api<Thresholds>("/config/risk-thresholds") });
  const [form, setForm] = useState<Thresholds | null>(null);
  useEffect(() => { if (current.data) setForm(current.data); }, [current.data]);
  const save = useMutation({
    mutationFn: () => api<Thresholds>("/config/risk-thresholds", { method: "PUT", body: form }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["thresholds"] }),
  });
  if (!form) return <Card title="Risk thresholds"><Loading /></Card>;
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <Card title="Risk thresholds">
      <form onSubmit={submit} className="space-y-3">
        <p className="text-xs text-slate-500">Minimum failure probability for each level. Applies to new predictions only.
          See the Models page for the precision/recall each threshold achieved on held-out data.</p>
        {(["medium", "high", "critical"] as const).map((k) => (
          <Field key={k} label={k[0]!.toUpperCase() + k.slice(1)}>
            <Input type="number" min={0.001} max={1} step={0.001} value={form[k]}
              onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
          </Field>
        ))}
        <Button type="submit" busy={save.isPending}>Save thresholds</Button>
        {save.isSuccess && <p className="text-xs text-emerald-700" role="status">Saved.</p>}
        <ErrorBox error={save.error} />
      </form>
    </Card>
  );
}
