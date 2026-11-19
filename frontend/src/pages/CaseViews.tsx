import { useState } from "react";
import { AgentPanel, fmtLocation } from "../components/Forensics";
import { ConcernBadge, Disclaimer, EmptyState, ErrorState, LoadingBlock, SeverityBadge } from "../components/ui";
import { api, downloadBlob, fmtBytes, fmtDate, type AgentResult } from "../lib/api";
import { useCase } from "../lib/caseContext";
import { useAsync } from "../lib/hooks";

/* ---------------- Multi-agent investigation (feature 7) ---------------- */
const AGENTS = ["Image Forensics Agent", "Video Analysis Agent", "Audio Analysis Agent", "Text Analysis Agent", "Metadata Verification Agent", "Lead Investigation Agent"];
const agentOf = (r: AgentResult) => AGENTS.find((a) => r.agent_name.startsWith(a)) ?? r.agent_name;

export function MultiAgentInvestigation() {
  const { results, evidence } = useCase();
  const [open, setOpen] = useState<string>("Lead Investigation Agent");
  if (!results) return <EmptyState title="No completed investigation yet">Upload evidence and run the investigation to see each agent's findings.</EmptyState>;
  const label = (id: string | null) => evidence.find((e) => e.id === id)?.label ?? "all evidence";
  const s = results.summary;
  const byAgent = AGENTS.map((a) => ({ agent: a, runs: results.agent_results.filter((r) => agentOf(r) === a) }));
  const lead = results.agent_results.find((r) => r.agent_name === "Lead Investigation Agent");
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <Disclaimer />
      <section className="card p-5" aria-labelledby="lead-h">
        <h2 id="lead-h" className="font-semibold">Lead Investigation Agent – unified result</h2>
        <p className="text-lg mt-2">Overall: <ConcernBadge level={s.overall} /></p>
        <p className="text-sm text-muted mt-1">{lead?.interpretation}</p>
        <div className="overflow-x-auto mt-3">
          <table className="w-full text-sm"><caption className="sr-only">Per-evidence result</caption>
            <thead><tr className="text-left text-muted"><th className="pr-3">Evidence</th><th className="pr-3">Concern</th><th className="pr-3">AI model result</th><th className="pr-3">Confidence</th><th>Uncertainty</th></tr></thead>
            <tbody>{Object.entries(s.assessments ?? {}).map(([id, a]) => {
              const ai = a.ai_detection as any;
              return (
                <tr key={id} className="border-t border-line/40 align-top">
                  <td className="py-2 pr-3"><span className="chip text-gold">{a.label}</span></td>
                  <td className="pr-3"><ConcernBadge level={a.concern_level} /></td>
                  <td className="pr-3 text-xs">{ai ? ai.verdict_text : "–"}</td>
                  <td className="pr-3 text-xs">{ai?.confidence?.value != null ? `${ai.confidence.level} (${Math.round(ai.confidence.value * 100)}%)` : "not stated"}</td>
                  <td className="text-xs">{a.uncertainty}</td>
                </tr>);
            })}</tbody>
          </table>
        </div>
        {!!s.disagreements?.length && (<div className="mt-3"><h3 className="label">Disagreements between agents (kept for review)</h3><ul className="list-disc pl-5 text-sm grid gap-1">{s.disagreements.map((d) => <li key={d}>{d}</li>)}</ul></div>)}
      </section>
      <section className="card p-4" aria-labelledby="xf-h">
        <h2 id="xf-h" className="font-semibold mb-2">Cross-modal inconsistencies found by the Lead Investigation Agent</h2>
        {results.findings.length === 0 ? <p className="text-sm text-muted">No cross-modal findings (add text/captions or several media files to compare).</p> : (
          <ul className="grid gap-2 text-sm">{results.findings.map((f) => (
            <li key={f.id} className="border-t border-line/40 pt-2"><span className={`chip mr-2 ${f.effect === "strengthens" ? "text-warn" : f.effect === "weakens" ? "text-ok" : "text-muted"}`}>
              {f.effect === "strengthens" ? "↑ inconsistency" : f.effect === "weakens" ? "↓ consistent" : "· context"}</span>{f.description}</li>))}</ul>)}
      </section>
      <section aria-labelledby="ag-h" className="grid gap-3">
        <h2 id="ag-h" className="font-semibold">Specialised agents</h2>
        <div role="tablist" aria-label="Agents" className="flex flex-wrap gap-1">
          {byAgent.map(({ agent, runs }) => (
            <button key={agent} role="tab" aria-selected={open === agent} className={`btn text-xs ${open === agent ? "btn-primary" : "btn-ghost"}`} onClick={() => setOpen(agent)}>
              {agent} <span className="mono">({runs.length})</span></button>))}
        </div>
        {byAgent.filter((b) => b.agent === open).map(({ agent, runs }) => runs.length === 0
          ? <p key={agent} className="card p-4 text-sm text-muted">{agent} had no evidence of its type in this case.</p>
          : runs.map((r) => <div key={r.id}><p className="text-xs text-muted mb-1">Evidence: {label(r.evidence_id)}</p><AgentPanel r={r} /></div>))}
      </section>
    </div>
  );
}

/* ---------------- Metadata & authenticity (feature 6) ---------------- */
export function MetadataAuthenticity() {
  const { caseItem, evidence } = useCase();
  const v = useAsync(() => api.vault(caseItem!.id), [caseItem?.id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Record<string, string>>({});
  const verify = async (id: string) => {
    setBusy(id);
    try { const r = await api.verify(id); setMsg((m) => ({ ...m, [id]: r.intact ? "✓ Intact – current SHA-256 matches the upload hash" : "✕ MISMATCH – stored file differs from upload" })); v.reload(); }
    catch (e) { setMsg((m) => ({ ...m, [id]: (e as Error).message })); } finally { setBusy(null); }
  };
  if (v.loading) return <LoadingBlock />;
  if (v.error) return <ErrorState message={v.error} onRetry={v.reload} />;
  return (
    <section className="grid gap-3" aria-labelledby="md-h">
      <div className="card p-4"><h2 id="md-h" className="font-semibold">Metadata & authenticity checks</h2>
        <p className="text-sm text-muted mt-1">EXIF / container metadata, timestamps, Content Credentials markers and SHA-256 hashes. Metadata can be stripped or forged – it never proves a file is genuine on its own. Originals are stored write-protected and are never modified.</p></div>
      {!v.data?.items.length ? <EmptyState title="No evidence uploaded" /> : v.data.items.filter((it) => !it.removed_at).map((it) => {
        const meta = (evidence.find((e) => e.id === it.id)?.file_metadata ?? {}) as Record<string, unknown>;
        const rows = Object.entries(meta).filter(([k]) => !["generator_hits", "original_filename", "mime_type"].includes(k));
        return (
          <article key={it.id} className="card p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2"><span className="chip text-gold">{it.label}</span><strong className="truncate">{it.filename}</strong><span className="text-muted">{it.media_type} · {fmtBytes(it.size_bytes)} · uploaded {fmtDate(it.uploaded_at)}</span>
              <button className="btn btn-ghost text-xs ml-auto" disabled={busy === it.id} onClick={() => verify(it.id)}>{busy === it.id ? "Verifying…" : "Verify SHA-256 integrity"}</button></div>
            {msg[it.id] && <p role="status" className={`text-sm mt-1 ${msg[it.id].startsWith("✓") ? "text-ok" : "text-bad"}`}>{msg[it.id]}</p>}
            <table className="w-full text-xs mt-2"><caption className="sr-only">Hash history for {it.label}</caption>
              <thead><tr className="text-left text-muted"><th className="pr-2">When</th><th className="pr-2">Purpose</th><th className="pr-2">SHA-256</th><th>Matches original</th></tr></thead>
              <tbody>{it.hashes.map((h, i) => <tr key={i} className="border-t border-line/40"><td className="pr-2 py-1">{fmtDate(h.computed_at)}</td><td className="pr-2">{h.purpose}</td><td className="pr-2 mono break-all">{h.digest}</td><td>{h.matches_original ? "✓ yes" : "✕ no"}</td></tr>)}</tbody>
            </table>
            <details className="mt-2"><summary className="cursor-pointer text-cyan text-xs font-semibold">File metadata ({rows.length} fields{rows.length === 0 ? " – run the investigation to extract" : ""})</summary>
              <table className="w-full text-xs mt-2"><tbody>{rows.map(([k, val]) => <tr key={k} className="border-t border-line/40"><th scope="row" className="text-left text-muted py-1 pr-4 font-medium align-top">{k}</th><td className="mono break-all">{typeof val === "object" ? JSON.stringify(val) : String(val)}</td></tr>)}</tbody></table>
            </details>
          </article>);
      })}
    </section>
  );
}

/* ---------------- History & traceability (used inside the report page) ---------------- */
const ACTION_LABEL: Record<string, string> = {
  "case.created": "Investigation created", "case.updated": "Investigation updated", "evidence.uploaded": "Evidence uploaded", "evidence.rejected": "Upload rejected",
  "evidence.verified": "Integrity verified", "evidence.accessed": "Original accessed", "evidence.removed": "Evidence removed", "analysis.started": "Analysis started",
  "analysis.completed": "Analysis completed", "analysis.failed": "Analysis failed", "report.generated": "Report generated",
  "report.downloaded": "Report downloaded",
};

export function HistoryTrail() {
  const { caseItem, results } = useCase();
  const t = useAsync(() => api.timeline(caseItem!.id), [caseItem?.id, results?.id]);
  if (t.loading) return <LoadingBlock />;
  if (t.error) return <ErrorState message={t.error} onRetry={t.reload} />;
  return (
    <section className="card p-5" aria-labelledby="tl-h">
      <h2 id="tl-h" className="font-semibold mb-3">Investigation history & evidence traceability</h2>
      <ol className="relative border-l border-line ml-3 grid gap-3 max-h-96 overflow-auto">
        {(t.data ?? []).map((e, i) => (
          <li key={i} className="ml-5"><p className="text-xs text-muted"><time dateTime={e.at}>{fmtDate(e.at)}</time> · {e.actor ?? "system"}</p><p className="text-sm font-medium">{ACTION_LABEL[e.action] ?? e.action}</p></li>))}
      </ol>
    </section>
  );
}

/* ---------------- Forensic report (feature 8) ---------------- */
export function ReportViewer() {
  const { caseItem, results } = useCase();
  const reps = useAsync(() => api.reports(caseItem!.id), [caseItem?.id]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const generate = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.generateReport(caseItem!.id); await reps.reload(); setSel(r.id); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const report = reps.data?.find((r) => r.id === (sel ?? reps.data?.[0]?.id));
  const c = report?.content;
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <section className="card p-4 flex flex-wrap items-center gap-3">
        <div className="grow"><h2 className="font-semibold">Forensic report</h2><p className="text-sm text-muted">{results ? "Evidence references, hashes, AI findings with confidence, suspicious timestamps/regions, cross-modal results, agent findings, human decisions and the audit trail." : "Run the investigation first – a report now would contain evidence and integrity information only."}</p></div>
        <button className="btn btn-gold" onClick={generate} disabled={busy}>{busy ? "Generating PDF…" : "Generate report"}</button>
        {err && <p role="alert" className="text-bad text-sm w-full">{err}</p>}
      </section>
      {reps.loading ? <LoadingBlock /> : reps.error ? <ErrorState message={reps.error} onRetry={reps.reload} /> : !reps.data?.length ? <EmptyState title="No reports generated yet" /> : (
        <div className="grid grid-cols-[minmax(0,1fr)] lg:grid-cols-[260px_minmax(0,1fr)] gap-4">
          <nav aria-label="Generated reports" className="card p-3"><ul className="grid gap-1">{reps.data.map((r) => (
            <li key={r.id}><button className={`w-full text-left rounded-lg px-3 py-2 text-sm ${report?.id === r.id ? "bg-panel2 text-cyan" : "hover:bg-panel"}`} aria-current={report?.id === r.id} onClick={() => setSel(r.id)}>{fmtDate(r.created_at)}<span className="block mono text-[10px] text-muted truncate">{r.sha256}</span></button></li>))}</ul></nav>
          {report && c && (
            <article className="card p-5 grid gap-4" aria-labelledby="rv-h">
              <header className="flex flex-wrap items-start gap-3">
                <div className="grow"><h2 id="rv-h" className="text-lg font-bold">Report · {c.case?.reference}</h2><p className="text-xs text-muted">Generated {fmtDate(report.created_at)} by {c.generated_by} · PDF SHA-256 <span className="mono break-all">{report.sha256}</span></p></div>
                <button className="btn btn-primary" onClick={() => downloadBlob(`/api/reports/${report.id}/download`, `${c.case?.reference}-report.pdf`)}>Download PDF</button>
              </header>
              <Disclaimer compact />
              <section><h3 className="label">Unified result</h3><p className="text-sm">Overall: <ConcernBadge level={c.job?.summary?.overall} /> · {c.indicators?.length ?? 0} indicators · {c.findings?.length ?? 0} cross-modal findings</p></section>
              <section><h3 className="label">Evidence register</h3>
                <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-left text-muted"><th className="pr-2">Ref</th><th className="pr-2">File</th><th className="pr-2">SHA-256</th><th>Integrity</th></tr></thead>
                  <tbody>{c.evidence?.map((e: any) => <tr key={e.id} className="border-t border-line/40"><td className="pr-2 py-1">{e.label}</td><td className="pr-2">{e.original_filename}</td><td className="pr-2 mono break-all">{e.sha256}</td><td>{e.integrity}</td></tr>)}</tbody></table></div></section>
              <section><h3 className="label">Flagged timestamps / regions</h3><ul className="text-sm grid gap-1">{c.indicators?.filter((i: any) => i.severity !== "info").map((i: any) => <li key={i.id}><SeverityBadge severity={i.severity} /> <strong>{i.evidence_label}</strong> · {i.description} <span className="text-muted text-xs">({fmtLocation(i.location)})</span></li>)}</ul></section>
              <section><h3 className="label">Analysis history</h3><ul className="text-xs mono grid gap-1">{c.history?.map((h: any) => <li key={h.id}>{h.id} · {h.status} · {h.started_at ?? "—"}</li>)}</ul></section>
            </article>
          )}
        </div>
      )}
      <HistoryTrail />
    </div>
  );
}
