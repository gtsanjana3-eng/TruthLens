import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { ConcernBadge, Disclaimer, EmptyState, ErrorState, LoadingBlock, StatusBadge } from "../components/ui";
import { api, fmtBytes, fmtDate, type Evidence, type MediaType } from "../lib/api";
import { CaseProvider, useCase } from "../lib/caseContext";
import { useAsync } from "../lib/hooks";
import { useConfirm } from "../components/ConfirmDialog";

const TABS = [
  ["", "Overview"], ["upload", "Evidence upload"], ["image", "Image forensics"], ["video", "Video deepfake"], ["audio", "AI voice"], ["text", "AI text"],
  ["metadata", "Metadata & authenticity"], ["investigation", "Multi-agent investigation"], ["report", "Forensic report"],
] as const;

export function CaseRoute() {
  const { caseId = "" } = useParams();
  return <CaseProvider caseId={caseId}><CaseFrame /></CaseProvider>;
}

function AnalysisBar() {
  const { activeJob, startAnalysis, evidence, caseItem, analysisError, results } = useCase();
  const canRun = evidence.length > 0 || !!caseItem?.context_statement.trim();
  return (
    <section aria-label="Analysis control" className="card p-4 flex flex-wrap items-center gap-4">
      {activeJob ? (
        <div className="grow min-w-60" aria-live="polite">
          <p className="text-sm font-semibold">Analysis {activeJob.status}: <span className="text-cyan">{activeJob.stage}</span></p>
          <div className="h-2 mt-2 rounded bg-navy overflow-hidden" role="progressbar" aria-label="Analysis progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(activeJob.progress * 100)}>
            <div className="h-full bg-cyan transition-all" style={{ width: `${Math.max(3, activeJob.progress * 100)}%` }} />
          </div>
        </div>
      ) : (
        <div className="grow text-sm">
          {results ? <>Latest analysis <StatusBadge status={results.status} /> finished {fmtDate(results.finished_at)} · triage <ConcernBadge level={results.summary.overall} /></> : <span className="text-muted">No analysis has been run for this case yet.</span>}
        </div>
      )}
      <button className="btn btn-primary" disabled={!!activeJob || !canRun} onClick={startAnalysis} title={canRun ? undefined : "Upload evidence first"}>
        {activeJob ? "Analysing…" : results ? "Re-run multi-agent investigation" : "Run multi-agent investigation"}
      </button>
      {analysisError && <p role="alert" className="text-bad text-sm w-full">{analysisError}</p>}
    </section>
  );
}

function CaseFrame() {
  const { caseItem, loading, error, reload } = useCase();
  if (loading) return <LoadingBlock label="Loading case" />;
  if (error || !caseItem) return <ErrorState message={error ?? "Case not found"} onRetry={reload} />;
  return (
    <div className="fade-up">
      <nav aria-label="Breadcrumb" className="text-xs text-muted mb-2"><Link to="/app" className="hover:text-cyan">Dashboard</Link> / <span className="mono">{caseItem.reference}</span></nav>
      <header className="flex flex-wrap items-start gap-3 mb-4">
        <div className="grow">
          <h1 className="text-2xl font-bold">{caseItem.title}</h1>
          <p className="text-sm text-muted mt-1"><span className="mono">{caseItem.reference}</span> · <StatusBadge status={caseItem.status} /> · priority {caseItem.priority} · opened {fmtDate(caseItem.created_at)}</p>
        </div>
      </header>
      <nav aria-label="Case sections" className="overflow-x-auto mb-4 -mx-1">
        <ul className="flex gap-1 min-w-max px-1 border-b border-line">
          {TABS.map(([to, label]) => (
            <li key={to}><NavLink end={to === ""} to={to} className={({ isActive }) => `block px-3 py-2 text-sm font-medium border-b-2 -mb-px ${isActive ? "border-cyan text-cyan" : "border-transparent text-muted hover:text-text"}`}>{label}</NavLink></li>
          ))}
        </ul>
      </nav>
      <div className="grid gap-4 min-w-0 grid-cols-[minmax(0,1fr)]">
        <AnalysisBar />
        <Outlet />
      </div>
    </div>
  );
}

const MEDIA_ICON: Record<MediaType, string> = { image: "🖼", video: "🎞", audio: "🎙", text: "📄", other: "📦" };

export function EvidenceCard({ e, onRemove }: { e: Evidence; onRemove?: () => void }) {
  return (
    <li className="card p-4 text-sm grid gap-1">
      <div className="flex items-center gap-2">
        <span aria-hidden="true">{MEDIA_ICON[e.media_type]}</span>
        <span className="chip text-gold">{e.label}</span>
        <span className="font-semibold truncate" title={e.original_filename}>{e.original_filename}</span>
      </div>
      <p className="text-muted text-xs">{e.media_type} · {fmtBytes(e.size_bytes)} · {fmtDate(e.created_at)}</p>
      <p className="mono text-[11px] text-muted break-all" title="SHA-256 recorded at upload">SHA-256 {e.sha256}</p>
      {e.description && <p className="text-xs">{e.description}</p>}
      <div className="flex gap-2 mt-2">
        <Link className="btn btn-ghost text-xs" to={e.media_type === "other" ? `/app/cases/${e.case_id}/metadata` : `/app/cases/${e.case_id}/${e.media_type}?e=${e.id}`}>{e.media_type === "other" ? "View metadata" : "Open workspace"}</Link>
        {onRemove && <button className="btn btn-danger text-xs" onClick={onRemove} aria-label={`Remove ${e.label} from case`}>Remove</button>}
      </div>
    </li>
  );
}

export function CaseOverview() {
  const { caseItem, evidence, results, reload } = useCase();
  const nav = useNavigate();
  const [status, setStatus] = useState(caseItem!.status);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, confirmDialog] = useConfirm();
  const [deleting, setDeleting] = useState(false);
  const changeStatus = async (s: string) => {
    setStatus(s as typeof status);
    try { await api.updateCase(caseItem!.id, { status: s as typeof status }); reload(); } catch (e) { setMsg((e as Error).message); }
  };
  const del = async () => {
    const ok = await confirm({ title: `Delete case ${caseItem!.reference}?`, danger: true, confirmLabel: "Delete case",
      message: <>“{caseItem!.title}” will be removed from your cases. Its record and evidence are kept in the integrity vault until the retention policy purges them.</> });
    if (!ok) return;
    setDeleting(true); setMsg(null);
    try { await api.deleteCase(caseItem!.id); nav("/app"); }
    catch (e) { setMsg(`Could not delete the case: ${(e as Error).message}`); setDeleting(false); }
  };
  const assessments = Object.entries(results?.summary.assessments ?? {});
  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 grid gap-4 content-start">
        {caseItem!.context_statement && <section className="card p-4" aria-labelledby="ctx-h"><h2 id="ctx-h" className="label">Claimed context</h2><p className="text-sm">{caseItem!.context_statement}</p></section>}
        <section aria-labelledby="ev-h">
          <h2 id="ev-h" className="font-semibold mb-2">Evidence ({evidence.length})</h2>
          {evidence.length === 0 ? <EmptyState title="No evidence attached" action={<Link className="btn btn-primary" to="upload">Upload evidence</Link>}>Add images, videos, audio recordings, captions or transcripts.</EmptyState> : (
            <ul className="grid sm:grid-cols-2 gap-3">{evidence.map((e) => <EvidenceCard key={e.id} e={e} />)}</ul>
          )}
        </section>
        {assessments.length > 0 && (
          <section className="card p-4" aria-labelledby="as-h">
            <h2 id="as-h" className="font-semibold mb-3">Triage by evidence item</h2>
            <ul className="grid gap-2">{assessments.map(([id, a]) => (
              <li key={id} className="flex flex-wrap items-center gap-2 text-sm border-b border-line/50 pb-2"><span className="chip text-gold">{a.label}</span><ConcernBadge level={a.concern_level} /><span className="text-muted">uncertainty: {a.uncertainty}</span><span className="text-muted">· {a.indicator_count} indicator(s)</span>{a.ai_detection?.status === "completed" && <span className="chip text-gold">AI model: {a.ai_detection.verdict?.replace("_", " ")} ({a.ai_detection.mean_p_fake?.toFixed(2)})</span>}</li>
            ))}</ul>
            <Link className="btn btn-ghost mt-3 text-xs" to="investigation">Open multi-agent investigation</Link>
          </section>
        )}
        <Disclaimer compact />
      </div>
      <aside className="grid gap-4 content-start" aria-label="Case management">
        <section className="card p-4 grid gap-3">
          <label htmlFor="case-status" className="label">Case status</label>
          <select id="case-status" className="input" value={status} onChange={(e) => changeStatus(e.target.value)}>
            <option value="open">Open</option><option value="in_review">In review</option><option value="closed">Closed</option>
          </select>
          <button className="btn btn-danger" onClick={del} disabled={deleting}>{deleting ? "Deleting…" : "Delete case"}</button>
          {msg && <p role="alert" className="text-xs text-bad">{msg}</p>}
          {confirmDialog}
        </section>
      </aside>
    </div>
  );
}


interface QueueItem { id: number; file: File; status: "pending" | "uploading" | "done" | "error"; message?: string }

export function UploadCenter() {
  const { caseItem, evidence, reload } = useCase();
  const caps = useAsync(() => api.capabilities(), []);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [desc, setDesc] = useState("");
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);
  const add = (files: FileList | null) => {
    if (!files?.length) return;
    const items: QueueItem[] = Array.from(files).map((file) => {
      // Any extension is accepted; the server classifies by content (file signature), not by name.
      const empty = file.size === 0;
      return { id: ++seq.current, file, status: empty ? "error" : "pending", message: empty ? "File is empty – nothing to preserve." : undefined };
    });
    setQueue((q) => [...q, ...items]);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); };
  // Uploads start automatically, one at a time, as soon as files are chosen or dropped.
  useEffect(() => {
    if (!caseItem || queue.some((q) => q.status === "uploading")) return;
    const next = queue.find((q) => q.status === "pending");
    if (!next) return;
    const set = (patch: Partial<QueueItem>) => setQueue((q) => q.map((it) => (it.id === next.id ? { ...it, ...patch } : it)));
    set({ status: "uploading" });
    api.upload(caseItem.id, next.file, desc)
      .then((ev) => {
        const kind = ev.media_type === "other" ? "unrecognised type – preserved, metadata & hash only" : ev.media_type;
        set({ status: "done", message: `Stored as ${ev.label} (${kind}) · SHA-256 ${ev.sha256?.slice(0, 16)}…` });
        reload();
      })
      .catch((e: Error) => set({ status: "error", message: e.message }));
  }, [queue, caseItem, desc, reload]);
  const retry = (id: number) => setQueue((q) => q.map((it) => (it.id === id ? { ...it, status: "pending", message: undefined } : it)));
  const [confirm, confirmDialog] = useConfirm();
  const [removeMsg, setRemoveMsg] = useState<string | null>(null);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const shown = evidence.filter((e) => !removed.has(e.id));
  const remove = async (ev: Evidence) => {
    const ok = await confirm({ title: `Remove ${ev.label} from this case?`, danger: true, confirmLabel: "Remove evidence",
      message: <>“{ev.original_filename}” will no longer appear in this case. The original file and its SHA-256 record stay in the integrity vault until the retention policy purges them.</> });
    if (!ok) return;
    setRemoveMsg(null);
    setRemoved((r) => new Set(r).add(ev.id)); // hide at once; restored below if the server refuses
    try { await api.removeEvidence(ev.id); setRemoveMsg(`${ev.label} removed from the case.`); reload(); }
    catch (e) {
      setRemoved((r) => { const n = new Set(r); n.delete(ev.id); return n; });
      setRemoveMsg(`Could not remove ${ev.label}: ${(e as Error).message}`);
    }
  };
  const l = caps.data?.limits;
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <section className="card p-5" aria-labelledby="up-h">
        <h2 id="up-h" className="font-semibold">Evidence upload center</h2>
        <p className="text-sm text-muted mt-1">Any file type is accepted. Content is identified from the file signature (not the extension), hashed with SHA-256 and stored write-protected. Originals are never modified.</p>
        {l && <p className="text-xs text-muted mt-1">Limits: images {l.image_mb} MB · video {l.video_mb} MB / {l.video_seconds}s · audio {l.audio_mb} MB / {l.audio_seconds}s · text {l.text_mb} MB</p>}
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
          className={`mt-4 rounded-xl border-2 border-dashed p-8 text-center ${drag ? "border-cyan bg-panel2" : "border-line"}`}>
          <p className="font-semibold">Drag & drop evidence files here</p>
          <p className="text-sm text-muted my-2">All file extensions accepted. Images, video, audio and text are analysed; any other file is preserved with SHA-256 and metadata checks.</p>
          <p className="text-xs text-muted mb-2">Uploads start automatically.</p>
          <button type="button" className="btn btn-ghost" onClick={() => input.current?.click()}>Browse files</button>
          <label htmlFor="file-input" className="sr-only">Choose evidence files</label>
          <input id="file-input" ref={input} type="file" multiple className="sr-only" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </div>
        <div className="mt-4"><label htmlFor="ev-desc" className="label">Description applied to this batch (optional)</label><input id="ev-desc" className="input" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={2000} placeholder="Source, how it was obtained, chain of custody notes…" /></div>
        {queue.length > 0 && (
          <>
            <ul className="grid gap-2 mt-4" aria-live="polite">
              {queue.map((q) => (
                <li key={q.id} className="flex flex-wrap items-center gap-3 text-sm border-b border-line/50 pb-2">
                  <span className="grow truncate">{q.file.name} <span className="text-muted">({fmtBytes(q.file.size)})</span></span>
                  <StatusBadge status={q.status === "done" ? "completed" : q.status === "error" ? "failed" : q.status === "uploading" ? "running" : "queued"} />
                  {q.status === "error" && q.file.size > 0 && <button className="btn btn-ghost text-xs" onClick={() => retry(q.id)}>Retry</button>}
                  {q.message && <span role={q.status === "error" ? "alert" : undefined} className={`text-xs w-full ${q.status === "error" ? "text-bad" : "text-muted"}`}>{q.message}</span>}
                </li>
              ))}
            </ul>
            <div className="flex gap-2 mt-3">
              <button className="btn btn-ghost" disabled={queue.some((q) => q.status === "uploading")} onClick={() => setQueue([])}>Clear list</button>
            </div>
          </>
        )}
      </section>
      <section aria-labelledby="att-h">
        <h2 id="att-h" className="font-semibold mb-2">Attached evidence</h2>
        {shown.length === 0 ? <EmptyState title="Nothing attached yet" /> : <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">{shown.map((e) => <EvidenceCard key={e.id} e={e} onRemove={() => remove(e)} />)}</ul>}
        {removeMsg && <p role="status" className={`text-sm mt-3 ${removeMsg.startsWith("Could not") ? "text-bad" : "text-muted"}`}>{removeMsg}</p>}
        {confirmDialog}
      </section>
    </div>
  );
}
