import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Role } from "./api/types";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import { AdminPage } from "./pages/AdminPage";
import { AlertsPage } from "./pages/AlertsPage";
import { ComparePage } from "./pages/ComparePage";
import { DashboardPage } from "./pages/DashboardPage";
import { EquipmentDetailPage } from "./pages/EquipmentDetailPage";
import { EquipmentListPage } from "./pages/EquipmentListPage";
import { IngestionPage } from "./pages/IngestionPage";
import { LoginPage } from "./pages/LoginPage";
import { ModelsPage } from "./pages/ModelsPage";

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { can } = useAuth();
  return can(role) ? <>{children}</> : <p className="text-sm text-slate-600">You don't have access to this page.</p>;
}

export function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="equipment" element={<EquipmentListPage />} />
        <Route path="equipment/compare" element={<ComparePage />} />
        <Route path="equipment/:id" element={<EquipmentDetailPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="ingestion" element={<RequireRole role="engineer"><IngestionPage /></RequireRole>} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="admin" element={<RequireRole role="admin"><AdminPage /></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
