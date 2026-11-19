import { useId, type ReactNode } from "react";
import type { Indicator } from "../lib/api";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-muted">
      <svg className="spin h-4 w-4" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" /><path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="var(--color-cyan)" strokeWidth="3" /></svg>
      <span>{label}…</span>
    </span>
  );
}

export function LoadingBlock({ label }: { label?: string }) {
  return <div className="card p-8 flex justify-center"><Spinner label={label} /></div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="card p-5 border-bad/60 flex flex-wrap items-center justify-between gap-3">
      <p><span aria-hidden="true">⚠ </span><strong className="text-bad">Error:</strong> {message}</p>
      {onRetry && <button className="btn btn-ghost" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card p-8 text-center grid gap-3 justify-items-center">
      <svg aria-hidden="true" className="h-10 w-10 text-line" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
      <h3 className="font-semibold">{title}</h3>
      {children && <div className="text-muted text-sm max-w-md">{children}</div>}
      {action}
    </div>
  );
}

const SEV: Record<string, { color: string; icon: string }> = {
  high: { color: "text-bad", icon: "▲▲" },
  medium: { color: "text-warn", icon: "▲" },
  low: { color: "text-blue", icon: "●" },
  info: { color: "text-muted", icon: "ⓘ" },
};

/** Severity is always conveyed by text + symbol, never by colour alone. */
export function SeverityBadge({ severity }: { severity: Indicator["severity"] | string }) {
  const s = SEV[severity] ?? SEV.info;
  return <span className={`chip ${s.color}`}><span aria-hidden="true">{s.icon}</span>{severity.toUpperCase()}</span>;
}

const CONCERN: Record<string, string> = {
  "no notable indicators": "text-ok",
  "low concern": "text-blue",
  "moderate concern": "text-warn",
  "elevated concern": "text-bad",
};
export function ConcernBadge({ level }: { level?: string | null }) {
  if (!level) return <span className="chip text-muted">not analysed</span>;
  return <span className={`chip ${CONCERN[level] ?? "text-muted"}`}>{level}</span>;
}

export function StatusBadge({ status }: { status?: string | null }) {
  const map: Record<string, string> = { completed: "text-ok", partial: "text-warn", failed: "text-bad", running: "text-cyan", queued: "text-muted", skipped: "text-muted", unavailable: "text-muted", open: "text-cyan", in_review: "text-gold", closed: "text-muted" };
  if (!status) return <span className="chip text-muted">—</span>;
  return <span className={`chip ${map[status] ?? "text-muted"}`}>{status.replace("_", " ")}</span>;
}

export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = useId();
  const descId = error || hint ? `${id}-desc` : undefined;
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      {children(id, descId)}
      {error ? <p id={descId} className="text-bad text-xs mt-1" role="alert">{error}</p> : hint ? <p id={descId} className="text-muted text-xs mt-1">{hint}</p> : null}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="text-muted mt-1 text-sm">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function ScoreBar({ value, label }: { value: number | null; label: string }) {
  if (value === null || value === undefined) return <span className="text-muted text-xs">n/a</span>;
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label}>
      <div className="h-2 w-24 rounded bg-navy overflow-hidden" aria-hidden="true"><div className="h-full bg-gradient-to-r from-blue to-gold" style={{ width: `${pct}%` }} /></div>
      <span className="mono text-xs">{value.toFixed(2)}</span>
    </div>
  );
}

export function Disclaimer({ compact = false }: { compact?: boolean }) {
  return (
    <aside aria-label="Interpretation notice" className={`card border-gold/40 ${compact ? "p-3 text-xs" : "p-4 text-sm"} flex gap-3`}>
      <span aria-hidden="true" className="text-gold text-lg leading-none">⚖</span>
      <p className="text-muted"><strong className="text-gold">Indicators, not verdicts.</strong> Scores are uncalibrated heuristic signals. No single model or agent determines authenticity — every finding is open to qualified human review.</p>
    </aside>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="card p-4">
      <p className="text-muted text-xs uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      {sub && <p className="text-muted text-xs mt-1">{sub}</p>}
    </div>
  );
}
