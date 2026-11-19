// Typed API client. Tokens live in sessionStorage and are sent only as Authorization headers.
export type Role = "admin" | "investigator" | "reviewer";
export type MediaType = "image" | "video" | "audio" | "text" | "other";
export type JobStatus = "queued" | "running" | "completed" | "partial" | "failed";

export interface User { id: string; email: string; full_name: string; role: Role; preferences: Record<string, unknown>; created_at: string }
export interface CaseItem {
  id: string; reference: string; title: string; description: string; context_statement: string; status: "open" | "in_review" | "closed";
  priority: string; owner_id: string; created_at: string; updated_at: string; evidence_count: number; latest_job_status: JobStatus | null; overall_concern: string | null;
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Evidence {
  id: string; case_id: string; label: string; media_type: MediaType; original_filename: string; mime_type: string; size_bytes: number;
  description: string; file_metadata: Record<string, unknown>; uploaded_by: string; created_at: string; sha256: string | null; preview_available: boolean;
}
export interface Indicator {
  id: number; evidence_id: string; agent_name: string; category: string; indicator_type: string; severity: "info" | "low" | "medium" | "high";
  description: string; location: Record<string, any>; score: number | null; alternative_explanations: string[]; recommended_checks: string[];
}
export interface AgentResult {
  id: number; evidence_id: string | null; agent_name: string; agent_version: string; status: "completed" | "unavailable" | "failed" | "skipped";
  suspicion_score: number | null; confidence: number | null; observations: { label: string; value: any; unit?: string }[]; interpretation: string;
  signals: { name: string; value: any; weight: number; direction: string; note: string }[]; limitations: string[]; errors: string[];
  artifacts: Record<string, any>; duration_ms: number; created_at: string;
}
export interface Finding { id: number; finding_type: string; effect: "strengthens" | "weakens" | "neutral"; description: string; evidence_ids: string[]; details: Record<string, any>; confidence: number | null }
export interface Assessment {
  label: string; concern_level: string; concern_rank: number; indicator_count: number; max_severity: string; agents_flagging: string[]; agents_clean: string[];
  peak_heuristic_score: number | null; signal_quality: number | null; uncertainty: string; disagreements: string[]; unavailable_analyses: string[]; failed_analyses: string[]; statement: string;
  ai_detection?: { status: string; verdict?: string | null; verdict_text?: string | null; mean_p_fake?: number | null; model?: string | null } | null;
}
export interface Job {
  id: string; case_id: string; status: JobStatus; progress: number; stage: string; error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  summary: { overall?: string; assessments?: Record<string, Assessment>; disagreements?: string[]; indicator_count?: number; finding_count?: number; cross_modal?: string };
}
export interface JobDetail extends Job { agent_results: AgentResult[]; findings: Finding[]; indicators: Indicator[] }
export interface Report { id: string; case_id: string; job_id: string | null; sha256: string; created_at: string; content: Record<string, any> }

const TOKEN_KEY = "truthlens.token";
export const tokenStore = {
  get: () => { try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t: string | null) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: { field: string; message: string }[] = []) { super(message); }
}

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

/** Backend origin. Empty locally (Vite proxies /api); set VITE_API_BASE when the API is hosted elsewhere. */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = tokenStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { ...init, headers });
  } catch {
    throw new ApiError(0, API_BASE
      ? "Cannot reach the TruthLens server. If it was asleep it is waking up – please try again in about a minute."
      : "Cannot reach the TruthLens server. Check that the backend is running.");
  }
  if (res.status === 401 && token) onUnauthorized?.();
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    let fields: { field: string; message: string }[] = [];
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      if (Array.isArray(body.errors)) fields = body.errors;
    } catch { /* non-JSON */ }
    throw new ApiError(res.status, msg, fields);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.blob()) as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  register: (email: string, full_name: string, password: string) => request<{ access_token: string; user: User }>("/api/auth/register", { method: "POST", body: json({ email, full_name, password }) }),
  login: (email: string, password: string) => request<{ access_token: string; user: User }>("/api/auth/login", { method: "POST", body: json({ email, password }) }),
  me: () => request<User>("/api/auth/me"),
  updateMe: (body: Partial<Pick<User, "full_name" | "preferences">>) => request<User>("/api/auth/me", { method: "PATCH", body: json(body) }),
  changePassword: (current_password: string, new_password: string) => request<void>("/api/auth/me/password", { method: "POST", body: json({ current_password, new_password }) }),
  users: () => request<User[]>("/api/auth/users"),
  setRole: (id: string, role: Role) => request<User>(`/api/auth/users/${id}/role`, { method: "PATCH", body: json({ role }) }),
  capabilities: () => request<{ agents: { name: string; version: string; status: string }[]; learned_models: { name: string; status: string }[]; limits: Record<string, number> }>("/api/capabilities"),

  cases: (params: { q?: string; status?: string; page?: number; page_size?: number } = {}) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && sp.set(k, String(v)));
    return request<Page<CaseItem>>(`/api/cases?${sp}`);
  },
  createCase: (body: { title: string; description: string; context_statement: string; priority: string }) => request<CaseItem>("/api/cases", { method: "POST", body: json(body) }),
  getCase: (id: string) => request<CaseItem>(`/api/cases/${id}`),
  updateCase: (id: string, body: Partial<CaseItem>) => request<CaseItem>(`/api/cases/${id}`, { method: "PATCH", body: json(body) }),
  deleteCase: (id: string) => request<void>(`/api/cases/${id}`, { method: "DELETE" }),
  stats: () => request<{ cases_by_status: Record<string, number>; evidence_by_type: Record<string, number>; indicators_by_severity: Record<string, number>; jobs_by_status: Record<string, number>; recent_activity: { action: string; case_id: string; created_at: string; detail: Record<string, any> }[] }>("/api/cases/dashboard/stats"),
  members: (id: string) => request<{ user_id: string; email: string; full_name: string; role: string; can_edit: boolean }[]>(`/api/cases/${id}/members`),
  addMember: (id: string, email: string, can_edit: boolean) => request(`/api/cases/${id}/members`, { method: "POST", body: json({ email, can_edit }) }),

  evidence: (caseId: string) => request<Evidence[]>(`/api/cases/${caseId}/evidence`),
  upload: (caseId: string, file: File, description: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("description", description);
    return request<Evidence>(`/api/cases/${caseId}/evidence`, { method: "POST", body: fd });
  },
  removeEvidence: (id: string) => request<void>(`/api/evidence/${id}`, { method: "DELETE" }),
  verify: (id: string) => request<{ intact: boolean; original_sha256: string; current_sha256: string }>(`/api/evidence/${id}/verify`, { method: "POST" }),
  vault: (caseId: string) => request<{ items: { id: string; label: string; filename: string; media_type: MediaType; size_bytes: number; uploaded_at: string; upload_ip: string | null; removed_at: string | null; hashes: { digest: string; purpose: string; matches_original: boolean; computed_at: string }[] }[] }>(`/api/cases/${caseId}/vault`),

  analyze: (caseId: string) => request<Job>(`/api/cases/${caseId}/analyze`, { method: "POST" }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  jobs: (caseId: string) => request<Job[]>(`/api/cases/${caseId}/jobs`),
  latest: (caseId: string) => request<JobDetail | null>(`/api/cases/${caseId}/results/latest`),
  history: (page = 1, status = "") => request<Page<Job & { case_reference: string; case_title: string }>>(`/api/history?page=${page}${status ? `&status=${status}` : ""}`),

  timeline: (caseId: string) => request<{ type: string; action: string; at: string; actor: string | null; detail: Record<string, any>; evidence_id: string | null }[]>(`/api/cases/${caseId}/timeline`),
  graph: (caseId: string) => request<{ nodes: GraphNode[]; edges: GraphEdge[]; job_id: string | null }>(`/api/cases/${caseId}/graph`),

  reports: (caseId: string) => request<Report[]>(`/api/cases/${caseId}/reports`),
  generateReport: (caseId: string) => request<Report>(`/api/cases/${caseId}/reports`, { method: "POST" }),
  blob: (path: string) => request<Blob>(path),
};

export interface GraphNode { id: string; kind: "evidence" | "finding" | "context"; label: string; title: string; media_type?: MediaType; concern?: string | null; indicators?: number; effect?: string }
export interface GraphEdge { source: string; target: string; relation: "supports" | "conflicts" | "uncertain" | "derived_from"; rationale: string; weight: number }

export async function downloadBlob(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const fmtDate = (s: string | null | undefined) => (s ? new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z").toLocaleString() : "—");
export const fmtTime = (sec: number) => `${Math.floor(sec / 60)}:${(sec % 60).toFixed(2).padStart(5, "0")}`;
