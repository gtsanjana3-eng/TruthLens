import type { AgentResult } from "../lib/api";
import { useAuthedUrl } from "../lib/hooks";
import { TimelineStrip } from "./Charts";
import { ScoreBar } from "./ui";

/** The per-media AI model result is stored as "<Agent> (AI model)". */
export const isAIModel = (r: AgentResult) => r.agent_name.endsWith("(AI model)");

const VERDICT: Record<string, { icon: string; color: string; border: string; title: string }> = {
  likely_ai: { icon: "⚠", color: "text-bad", border: "border-bad/60", title: "Likely AI-generated / manipulated" },
  no_indication: { icon: "✓", color: "text-ok", border: "border-ok/60", title: "No indication from the validated model" },
  inconclusive: { icon: "?", color: "text-warn", border: "border-warn/60", title: "Inconclusive" },
  not_applicable: { icon: "∅", color: "text-muted", border: "border-line", title: "AI model not applicable to this content" },
  no_validated_detector: { icon: "∅", color: "text-muted", border: "border-line", title: "No validated model – score for reference only" },
  unavailable: { icon: "✕", color: "text-muted", border: "border-line", title: "AI model unavailable" },
};
const CONF: Record<string, string> = { high: "text-ok", medium: "text-warn", low: "text-bad", none: "text-muted" };

interface Detector {
  role: string; model: string; revision: string; url: string; scope: string; status: string; reason?: string; error?: string; decision_grade?: boolean;
  summary?: { n: number; mean: number; fraction_ge_0_7: number; verdict: string };
  validation?: { roc_auc: number; n: number; dataset: string; decision_grade: boolean } | null;
  windows?: { t_start: number; t_end: number; score: number }[];
  chunks?: { start: number; end: number; score: number; preview: string }[];
}
interface Evidence { t: number; frame: number; image: string; face_image?: string; score: number; suspicious: boolean }

function EvidenceThumb({ evidenceId, item, onSeek }: { evidenceId: string; item: Evidence; onSeek?: (t: number) => void }) {
  const { url } = useAuthedUrl(`/api/evidence/${evidenceId}/artifacts/${item.image}`);
  const { url: face } = useAuthedUrl(item.face_image ? `/api/evidence/${evidenceId}/artifacts/${item.face_image}` : null);
  return (
    <figure className={`card p-2 text-xs ${item.suspicious ? "border-bad/60" : ""}`}>
      <button className="flex gap-2 text-left" onClick={() => onSeek?.(item.t)} aria-label={`Jump to ${item.t.toFixed(2)} seconds`}>
        {url && <img src={url} alt={`Frame at ${item.t.toFixed(2)} s`} className="w-32 rounded" />}
        {face && <img src={face} alt={`Face the model analysed at ${item.t.toFixed(2)} s`} className="w-16 h-16 rounded object-cover" />}
      </button>
      <figcaption className="mt-1"><span className="mono">{item.t.toFixed(2)}s</span> · score <span className="mono">{item.score.toFixed(2)}</span>{item.suspicious && <strong className="text-bad"> · ⚑ flagged</strong>}</figcaption>
    </figure>
  );
}

/** One AI model per media type: verdict, measured confidence, score vs model accuracy, and where it looked. */
export function AIDetectionPanel({ result, onSeek, evidenceId, title }: { result?: AgentResult; onSeek?: (t: number, end?: number) => void; evidenceId?: string; title: string }) {
  if (!result) return null;
  const a = result.artifacts ?? {};
  const d = (a.detector ?? {}) as Detector;
  const v = VERDICT[a.verdict as string] ?? VERDICT.inconclusive;
  const conf = (a.confidence ?? { level: "none", value: null, basis: "" }) as { level: string; value: number | null; ci95?: number[]; basis: string };
  const frames = (a.frames ?? []) as { t: number; score?: number; suspicious?: boolean; face?: boolean }[];
  const scored = frames.filter((f) => f.score !== undefined);
  const evidence = (a.visual_evidence ?? []) as Evidence[];
  return (
    <section className={`card p-5 border-2 ${v.border}`} aria-labelledby="ai-h">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="ai-h" className="font-semibold mr-auto">{title}</h2>
        <span className="text-xs text-muted">{result.duration_ms} ms</span>
      </div>
      <p className={`mt-3 text-xl font-bold flex items-center gap-2 ${v.color}`} role="status"><span aria-hidden="true" className="text-2xl">{v.icon}</span>{v.title}</p>
      <p className="text-sm mt-1">{result.interpretation}</p>
      {result.errors.length > 0 && <p role="alert" className="text-bad text-xs mt-1">{result.errors.join("; ")}</p>}

      <dl className="grid sm:grid-cols-3 gap-4 mt-4 text-sm">
        <div className="card p-3">
          <dt className="label">Confidence in this decision</dt>
          <dd className={`text-lg font-bold capitalize ${CONF[conf.level] ?? "text-muted"}`}>{conf.value !== null ? `${conf.level} · ${Math.round(conf.value * 100)}%` : "not stated"}</dd>
          <dd className="text-xs text-muted mt-1">{conf.basis}</dd>
        </div>
        <div className="card p-3">
          <dt className="label">Model score for this file</dt>
          <dd>{d.summary ? <ScoreBar value={d.summary.mean} label="Model score for this file" /> : <span className="text-muted text-xs">{d.reason || d.error || "–"}</span>}</dd>
          <dd className="text-xs text-muted mt-1">Raw, uncalibrated model output (0 = looks real, 1 = looks AI). {d.summary && d.summary.n > 1 ? `Mean of ${d.summary.n} inputs.` : ""}</dd>
        </div>
        <div className="card p-3">
          <dt className="label">Model accuracy (measured separately)</dt>
          <dd className="text-lg font-bold">{d.validation ? `AUC ${d.validation.roc_auc.toFixed(2)}` : "not validated"}</dd>
          <dd className="text-xs text-muted mt-1">{d.validation ? `On ${d.validation.n} held-out labelled samples (${d.validation.dataset}). ` : ""}
            {d.decision_grade ? <span className="chip text-ok">✓ decision-grade</span> : <span className="chip text-warn">✕ reference only</span>}</dd>
        </div>
      </dl>

      {scored.length > 0 && (
        <div className="mt-4"><TimelineStrip values={scored.map((f) => f.score!)} times={scored.map((f) => f.t)} flagged={scored.map((f) => !!f.suspicious)}
          label="Face-swap score per sampled frame (▲ = flagged) – click to jump the player" onSelect={onSeek ? (i) => onSeek(scored[i].t) : undefined} /></div>
      )}
      {d.windows && d.windows.length > 0 && (
        <div className="mt-4"><TimelineStrip values={d.windows.map((w) => w.score)} times={d.windows.map((w) => w.t_start)} flagged={d.windows.map((w) => w.score >= 0.7)}
          label="Synthetic-voice score per 4-second window (▲ = ≥ 0.70) – click to play that window" onSelect={onSeek ? (i) => onSeek(d.windows![i].t_start, d.windows![i].t_end) : undefined} /></div>
      )}
      {d.chunks && d.chunks.length > 0 && (
        <div className="mt-4"><h3 className="label">AI-text score per passage</h3>
          <ul className="grid gap-1 text-xs">{d.chunks.map((c) => (
            <li key={c.start} className={`card p-2 ${c.score >= 0.7 ? "border-bad/60" : ""}`}><span className="mono mr-2">{c.score.toFixed(2)}</span>{c.score >= 0.7 && <strong className="text-bad">⚑ </strong>}“{c.preview}…”</li>))}</ul></div>
      )}
      {evidenceId && evidence.length > 0 && (
        <div className="mt-4"><h3 className="label">Visual evidence – the frames and faces the model judged (click to jump)</h3>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">{evidence.map((e) => <EvidenceThumb key={e.frame} evidenceId={evidenceId} item={e} onSeek={onSeek} />)}</div></div>
      )}
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-cyan font-semibold">How to read this result</summary>
        <ul className="list-disc pl-4 mt-2 grid gap-1 text-muted">
          <li>Model: <a className="text-cyan underline" href={d.url} target="_blank" rel="noreferrer noopener">{d.model}</a> (pinned revision {String(d.revision).slice(0, 10)}). Scope: {d.scope}.</li>
          <li>"Confidence" is how often this model was right when it made the same kind of call on labelled test data – not the raw score.</li>
          <li>Only decision-grade models (accuracy AUC ≥ {a.thresholds?.decision_grade_auc ?? 0.75} on the same media type) may decide; "likely AI" needs a score ≥ {a.thresholds?.likely_ai ?? 0.7}, "no indication" ≤ {a.thresholds?.no_indication ?? 0.3}.</li>
          {result.limitations.map((l) => <li key={l}>{l}</li>)}
        </ul>
      </details>
    </section>
  );
}
