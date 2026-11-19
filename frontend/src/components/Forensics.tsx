import { Fragment, useState } from "react";
import { fmtDate, type AgentResult, type Evidence, type Indicator } from "../lib/api";
import { ScoreBar, SeverityBadge, StatusBadge } from "./ui";

export function fmtLocation(loc: Record<string, any>): string {
  if (!loc) return "—";
  if (loc.t_start !== undefined) return `${Number(loc.t_start).toFixed(2)}s – ${Number(loc.t_end ?? loc.t_start).toFixed(2)}s${loc.frame !== undefined ? ` (frame ${loc.frame})` : ""}`;
  if (loc.x !== undefined) return `Region x=${loc.x}, y=${loc.y}, ${loc.w}×${loc.h}px`;
  if (loc.frames) return `Frames ${loc.frames.slice(0, 6).join(", ")}${loc.frames.length > 6 ? "…" : ""}`;
  if (loc.field) return `Metadata: ${loc.field}`;
  if (loc.offset_s !== undefined) return `Audio offset ${loc.offset_s}s`;
  return Object.entries(loc).slice(0, 3).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ");
}

function valueText(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(valueText).join(", ");
  if (typeof v === "object") return JSON.stringify(v).slice(0, 160);
  return String(v);
}

export function AgentPanel({ r }: { r: AgentResult }) {
  const [open, setOpen] = useState(false);
  const modelChecks = (r.artifacts?.model_checks ?? []) as { name: string; status: string; detail: string }[];
  const obs = r.observations.filter((o) => typeof o.value !== "object" || o.value === null || Array.isArray(o.value));
  return (
    <article className="card p-4" aria-labelledby={`ag-${r.id}`}>
      <header className="flex flex-wrap items-center gap-2 justify-between">
        <h3 id={`ag-${r.id}`} className="font-semibold">{r.agent_name} <span className="mono text-xs text-muted">v{r.agent_version}</span></h3>
        <div className="flex items-center gap-3"><StatusBadge status={r.status} /><ScoreBar value={r.suspicion_score} label={`${r.agent_name} heuristic score`} /></div>
      </header>
      <p className="text-xs text-muted mt-1">Run {fmtDate(r.created_at)} · {r.duration_ms} ms · signal quality {r.confidence ?? "n/a"} · score is an uncalibrated indicator</p>
      <div className="grid md:grid-cols-2 gap-4 mt-3 text-sm">
        <section aria-label="Observed evidence">
          <h4 className="label">Observed (measured)</h4>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            {obs.slice(0, open ? undefined : 6).map((o, i) => (<Fragment key={i}><dt className="text-muted">{o.label}</dt><dd className="mono text-xs break-all">{valueText(o.value)}{o.unit ? ` ${o.unit}` : ""}</dd></Fragment>))}
          </dl>
        </section>
        <section aria-label="Model interpretation">
          <h4 className="label">Interpretation</h4>
          <p>{r.interpretation || "—"}</p>
          {r.errors.length > 0 && <p role="alert" className="text-bad mt-2 text-xs">Errors: {r.errors.join("; ")}</p>}
        </section>
      </div>
      <button className="btn btn-ghost mt-3 text-xs" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"} signals, limitations & model status</button>
      {open && (
        <div className="grid md:grid-cols-2 gap-4 mt-3 text-sm">
          <div>
            <h4 className="label">Signals</h4>
            {r.signals.length ? (
              <table className="w-full text-xs"><caption className="sr-only">Signals for {r.agent_name}</caption>
                <thead><tr className="text-muted text-left"><th>Signal</th><th>Value</th><th>Weight</th><th>Direction</th></tr></thead>
                <tbody>{r.signals.map((s, i) => <tr key={i} className="border-t border-line/40"><td className="py-1 pr-2">{s.name}{s.note && <span className="block text-muted">{s.note}</span>}</td><td className="mono">{valueText(s.value)}</td><td className="mono">{s.weight}</td><td>{s.direction === "suspicious" ? "↑ suspicious" : s.direction === "reassuring" ? "↓ reassuring" : "· neutral"}</td></tr>)}</tbody>
              </table>) : <p className="text-muted text-xs">No weighted signals.</p>}
          </div>
          <div>
            <h4 className="label">Limitations</h4>
            <ul className="list-disc pl-4 text-xs text-muted grid gap-1">{r.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
            {modelChecks.length > 0 && <><h4 className="label mt-3">Learned-model status</h4><ul className="text-xs grid gap-1">{modelChecks.map((m) => <li key={m.name}><span className="chip text-muted">{m.status}</span> {m.name}</li>)}</ul></>}
          </div>
        </div>
      )}
    </article>
  );
}

/** What "Inspect" will do for this location – or null when there is nothing specific to show. */
export function inspectLabel(loc: Record<string, any>): string | null {
  if (!loc) return null;
  if (loc.x !== undefined) return "Show region on image";
  if (loc.char_start !== undefined) return "Highlight passage";
  if (loc.t_start !== undefined) return `Jump to ${Number(loc.t_start).toFixed(1)}s`;
  if (loc.frames?.length) return "Show flagged frames";
  if (loc.scope === "whole_image" || loc.scope === "face_crop") return "Show image";
  return null;
}

export function IndicatorCard({ ind, onSelect, label }: { ind: Indicator; onSelect?: () => void; label?: string }) {
  return (
    <li className="card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={ind.severity} />
        <span className="font-semibold capitalize">{ind.indicator_type.replaceAll("_", " ")}</span>
        {label && <span className="chip text-cyan">{label}</span>}
        <span className="text-xs text-muted ml-auto">{ind.agent_name}</span>
      </div>
      <p className="mt-2">{ind.description}</p>
      <p className="text-xs text-muted mt-1">Location: {fmtLocation(ind.location)}{ind.score !== null && ` · strength ${ind.score.toFixed(2)}`}</p>
      <details className="mt-2">
        <summary className="cursor-pointer text-cyan text-xs font-semibold">Alternative explanations & recommended checks</summary>
        <div className="grid sm:grid-cols-2 gap-3 mt-2 text-xs">
          <div><h4 className="label">Could also be explained by</h4><ul className="list-disc pl-4 grid gap-1">{ind.alternative_explanations.map((a) => <li key={a}>{a}</li>)}</ul></div>
          <div><h4 className="label">Evidence that would help distinguish</h4><ul className="list-disc pl-4 grid gap-1">{ind.recommended_checks.map((a) => <li key={a}>{a}</li>)}</ul></div>
        </div>
      </details>
      {onSelect && inspectLabel(ind.location) && (
        <div className="flex flex-wrap gap-2 mt-3"><button className="btn btn-ghost text-xs" onClick={onSelect}>{inspectLabel(ind.location)}</button></div>
      )}
    </li>
  );
}

export function EvidencePicker({ items, value, onChange }: { items: Evidence[]; value: string | null; onChange: (id: string) => void }) {
  if (items.length < 2) return null;
  return (
    <div className="mb-4 max-w-sm">
      <label htmlFor="ev-pick" className="label">Evidence item</label>
      <select id="ev-pick" className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {items.map((e) => <option key={e.id} value={e.id}>{e.label} · {e.original_filename}</option>)}
      </select>
    </div>
  );
}
