import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ConcernBadge, EmptyState, ErrorState, Field, LoadingBlock, PageHeader, StatusBadge } from "../components/ui";
import { api, ApiError, fmtDate } from "../lib/api";
import { useAsync } from "../lib/hooks";


export { Dashboard } from "./Dashboard";

export function NewCase() {
  const nav = useNavigate();
  const [form, setForm] = useState({ title: "", description: "", context_statement: "", priority: "medium" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.title.trim().length < 3) { setErrors({ title: "Title must be at least 3 characters" }); return; }
    setErrors({});
    setBusy(true);
    try {
      const c = await api.createCase(form);
      nav(`/app/cases/${c.id}/upload`);
    } catch (err) {
      const ae = err as ApiError;
      setError(ae.message);
      setErrors(Object.fromEntries(ae.fields.map((f) => [f.field, f.message])));
    } finally { setBusy(false); }
  };
  return (
    <div className="fade-up max-w-3xl">
      <PageHeader title="New investigation" subtitle="Create a case, then attach image, video, audio and text evidence." />
      <form onSubmit={submit} noValidate className="card p-6 grid gap-5">
        {error && <p role="alert" className="text-bad">{error}</p>}
        <Field label="Case title" error={errors.title}>{(id, d) => <input id={id} className="input" value={form.title} onChange={set("title")} aria-invalid={!!errors.title} aria-describedby={d} required maxLength={200} />}</Field>
        <Field label="Description" hint="Internal notes about the investigation scope.">{(id, d) => <textarea id={id} className="input min-h-24" value={form.description} onChange={set("description")} aria-describedby={d} maxLength={5000} />}</Field>
        <Field label="Claimed context statement" hint="What is claimed about the media (when, where, who, device). TruthLens extracts these claims and checks them against the evidence.">
          {(id, d) => <textarea id={id} className="input min-h-24" value={form.context_statement} onChange={set("context_statement")} aria-describedby={d} maxLength={10000} placeholder="e.g. 30-second video filmed at night on 15 March 2024 in Bengaluru with an iPhone." />}
        </Field>
        <Field label="Priority">{(id) => (
          <select id={id} className="input" value={form.priority} onChange={set("priority")}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select>
        )}</Field>
        <div className="flex gap-2"><button className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Create case & add evidence"}</button><Link to="/app" className="btn btn-ghost">Cancel</Link></div>
      </form>
    </div>
  );
}

export function History() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const h = useAsync(() => api.history(page, status), [page, status]);
  const pages = h.data ? Math.max(1, Math.ceil(h.data.total / h.data.page_size)) : 1;
  return (
    <div className="fade-up">
      <PageHeader title="Investigation history" subtitle="Every analysis run across your cases, newest first." actions={
        <div><label htmlFor="h-status" className="label">Filter by status</label>
          <select id="h-status" className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All</option><option value="completed">Completed</option><option value="partial">Partial</option><option value="failed">Failed</option><option value="running">Running</option>
          </select></div>} />
      {h.loading ? <LoadingBlock /> : h.error ? <ErrorState message={h.error} onRetry={h.reload} /> : !h.data?.items.length ? <EmptyState title="No analyses yet">Run an analysis from a case to see it here.</EmptyState> : (
        <div className="card p-4 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Analysis history</caption>
            <thead><tr className="text-left text-muted border-b border-line"><th className="py-2 pr-3">Case</th><th className="pr-3">Status</th><th className="pr-3">Triage</th><th className="pr-3">Indicators</th><th className="pr-3">Started</th><th>Finished</th></tr></thead>
            <tbody>{h.data.items.map((j) => (
              <tr key={j.id} className="border-b border-line/50">
                <td className="py-2 pr-3"><Link className="text-cyan hover:underline" to={`/app/cases/${j.case_id}/findings`}>{j.case_title}</Link> <span className="mono text-xs text-muted">{j.case_reference}</span></td>
                <td className="pr-3"><StatusBadge status={j.status} /></td>
                <td className="pr-3"><ConcernBadge level={j.summary?.overall} /></td>
                <td className="pr-3">{j.summary?.indicator_count ?? "—"}</td>
                <td className="pr-3 text-xs text-muted">{fmtDate(j.started_at)}</td>
                <td className="text-xs text-muted">{fmtDate(j.finished_at)}</td>
              </tr>))}
            </tbody>
          </table>
          <nav aria-label="History pages" className="flex justify-end gap-2 mt-3 items-center text-sm">
            <button className="btn btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
            <span aria-live="polite">Page {page} of {pages}</span>
            <button className="btn btn-ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
          </nav>
        </div>
      )}
    </div>
  );
}
