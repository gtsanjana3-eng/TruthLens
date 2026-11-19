import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/Layout";
import { LoadingBlock } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import { CaseOverview, CaseRoute, UploadCenter } from "./pages/CaseShell";
import { MetadataAuthenticity, MultiAgentInvestigation, ReportViewer } from "./pages/CaseViews";
import { Dashboard, History, NewCase } from "./pages/Main";
import { AuthPage, Landing } from "./pages/Public";
import { AudioWorkspace, ImageWorkspace, TextWorkspace, VideoWorkspace } from "./pages/Workspaces";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-8"><LoadingBlock label="Restoring session" /></div>;
  if (!user) return <Navigate to="/" replace state={{ from: loc.pathname }} />; // eye intro first, then sign-in
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/app" element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="cases/new" element={<NewCase />} />
        <Route path="cases/:caseId" element={<CaseRoute />}>
          <Route index element={<CaseOverview />} />
          <Route path="upload" element={<UploadCenter />} />
          <Route path="image" element={<ImageWorkspace />} />
          <Route path="video" element={<VideoWorkspace />} />
          <Route path="audio" element={<AudioWorkspace />} />
          <Route path="text" element={<TextWorkspace />} />
          <Route path="metadata" element={<MetadataAuthenticity />} />
          <Route path="investigation" element={<MultiAgentInvestigation />} />
          <Route path="report" element={<ReportViewer />} />
        </Route>
        <Route path="history" element={<History />} />
      </Route>
      <Route path="*" element={<div className="p-10 text-center"><h1 className="text-2xl font-bold">Page not found</h1><a className="text-cyan underline" href="/">Go home</a></div>} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter><AppRoutes /></BrowserRouter>
    </AuthProvider>
  );
}
