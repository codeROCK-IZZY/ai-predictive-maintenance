import clsx from "clsx";
import { NavLink, Outlet } from "react-router-dom";
import type { Role } from "../api/types";
import { useAuth } from "../lib/auth";

const NAV: { to: string; label: string; role: Role }[] = [
  { to: "/", label: "Dashboard", role: "viewer" },
  { to: "/equipment", label: "Equipment", role: "viewer" },
  { to: "/alerts", label: "Alerts", role: "viewer" },
  { to: "/ingestion", label: "Data ingestion", role: "engineer" },
  { to: "/models", label: "Models", role: "viewer" },
  { to: "/admin", label: "Administration", role: "admin" },
];

export function Layout() {
  const { user, logout, can } = useAuth();
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-slate-200 bg-slate-900 text-slate-200 md:min-h-screen md:w-60 md:border-b-0">
        <div className="px-5 py-4">
          <p className="text-sm font-semibold text-white">Predictive Maintenance</p>
          <p className="text-xs text-slate-400">Equipment health platform</p>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
          {NAV.filter((n) => can(n.role)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => clsx("whitespace-nowrap rounded-md px-3 py-2 text-sm",
                isActive ? "bg-slate-700 text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-end gap-3 border-b border-slate-200 bg-white px-6 py-3 text-sm">
          <span className="text-slate-600">{user?.email}</span>
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium capitalize text-slate-700">{user?.role}</span>
          <button onClick={() => void logout()} className="text-sky-700 hover:underline">Sign out</button>
        </header>
        <main className="mx-auto max-w-7xl p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
