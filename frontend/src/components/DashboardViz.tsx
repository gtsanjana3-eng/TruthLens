import { useEffect, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { fmtDate } from "../lib/api";

/* Creative dashboard building blocks. Every number shown comes from the API – nothing is simulated. */

const reduced = () => typeof window !== "undefined" && (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);

/** Animated count-up from 0 to the real value. */
export function useCountUp(value: number, ms = 900) {
  const [n, setN] = useState(reduced() ? value : 0);
  useEffect(() => {
    if (reduced()) { setN(value); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      setN(Math.round(value * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return n;
}

/** Card with a cursor-following spotlight glow. */
export function GlowCard({ children, className = "", style, as: Tag = "div" }: { children: ReactNode; className?: string; style?: CSSProperties; as?: "div" | "section" | "li" | "figure" }) {
  const onMove = (e: MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return <Tag className={`card glow-card ${className}`} style={style} onMouseMove={onMove}>{children}</Tag>;
}

const ICONS: Record<string, ReactNode> = {
  cases: <path d="M4 7h16v12H4zM9 7V5h6v2" />,
  evidence: <path d="M6 3h9l4 4v14H6zM14 3v5h5" />,
  indicators: <path d="M12 3l9 16H3zM12 10v4M12 17h.01" />,
  analyses: <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />,
};

export function StatTile({ label, value, sub, icon, accent = "var(--color-cyan)", delay = 0, loading }: { label: string; value: number; sub?: string; icon: keyof typeof ICONS; accent?: string; delay?: number; loading?: boolean }) {
  const n = useCountUp(loading ? 0 : value);
  return (
    <GlowCard className="p-4 rise-in relative overflow-hidden" style={{ animationDelay: `${delay}ms`, ["--accent" as string]: accent }}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-muted text-xs uppercase tracking-wider">{label}</p>
        <svg viewBox="0 0 24 24" className="w-8 h-8 p-1.5 rounded-lg stat-icon" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[icon]}</svg>
      </div>
      <p className="text-3xl font-extrabold mt-1 tabular-nums" aria-live="polite">{loading ? "…" : n.toLocaleString()}</p>
      {sub && <p className="text-muted text-xs mt-1">{sub}</p>}
      <span className="stat-bar" aria-hidden="true" />
    </GlowCard>
  );
}

/** Greeting banner with a live radar: each blip is one of your real cases, coloured by its triage level. */
export function CommandBanner({ name, openCases, activeJobs, blips, action }: {
  name: string; openCases: number; activeJobs: number; action: ReactNode;
  blips: { id: string; label: string; concern?: string | null }[];
}) {
  const h = new Date().getHours();
  const greet = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return (
    <section className="card command-banner relative overflow-hidden p-5 sm:p-7 mb-6 grid md:grid-cols-[1fr_auto] gap-6 items-center" aria-label="Overview">
      <div className="relative z-10">
        <p className="mono text-[11px] tracking-[.3em] text-cyan/90 flex items-center gap-2">
          <span className={`live-dot ${activeJobs ? "" : "idle"}`} aria-hidden="true" />{activeJobs ? `${activeJobs} ANALYSIS${activeJobs > 1 ? "ES" : ""} RUNNING` : "ALL AGENTS IDLE"}
        </p>
        <p className="text-2xl sm:text-4xl font-extrabold tracking-tight mt-3">{greet}, <span className="text-shimmer">{name.split(" ")[0]}</span></p>
        <p className="text-muted mt-2">{openCases ? <>You have <strong className="text-text">{openCases}</strong> open investigation{openCases > 1 ? "s" : ""}.</> : "No open investigations right now."}</p>
        <div className="flex flex-wrap gap-2 mt-5">{action}</div>
      </div>
      <Radar blips={blips} />
    </section>
  );
}

const CONCERN_COLOR: Record<string, string> = { "elevated concern": "var(--color-bad)", "moderate concern": "var(--color-warn)", "low concern": "var(--color-blue)", "no notable indicators": "var(--color-ok)" };

function hash(s: string) { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) / 4294967295; }

function Radar({ blips }: { blips: { id: string; label: string; concern?: string | null }[] }) {
  return (
    <div className="radar relative w-44 h-44 sm:w-52 sm:h-52 mx-auto shrink-0" role="img" aria-label={`Radar of ${blips.length} recent case(s) by triage level`}>
      <div className="radar-sweep" aria-hidden="true" />
      {[0.33, 0.66, 1].map((r) => <span key={r} className="radar-ring" style={{ inset: `${(1 - r) * 50}%` }} aria-hidden="true" />)}
      <span className="radar-cross" aria-hidden="true" />
      {blips.map((b) => {
        const a = hash(b.id) * Math.PI * 2, d = 0.25 + hash(b.id + "r") * 0.65;
        const color = CONCERN_COLOR[b.concern ?? ""] ?? "var(--color-cyan)";
        return (
          <Link key={b.id} to={`/app/cases/${b.id}`} className="radar-blip" title={`${b.label}${b.concern ? ` · ${b.concern}` : ""}`}
            style={{ left: `${50 + Math.cos(a) * d * 46}%`, top: `${50 + Math.sin(a) * d * 46}%`, background: color, color, animationDelay: `${(a / (Math.PI * 2)) * 4}s` }}>
            <span className="sr-only">{b.label}</span>
          </Link>
        );
      })}
    </div>
  );
}

/* Evidence by media type – donut. Fixed hue per type (validated for colour-vision deficiency on the dark panel). */
export const MEDIA_COLORS: Record<string, string> = { image: "#3987e5", video: "#d95926", audio: "#199e70", text: "#c98500", other: "#d55181" };
const MEDIA_ORDER = ["image", "video", "audio", "text", "other"];

export function DonutChart({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = MEDIA_ORDER.filter((k) => data[k]).map((k) => [k, data[k]] as const);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  const [hover, setHover] = useState<string | null>(null);
  const [drawn, setDrawn] = useState(reduced());
  useEffect(() => { const t = setTimeout(() => setDrawn(true), 60); return () => clearTimeout(t); }, []);
  const R = 52, C = 2 * Math.PI * R, GAP = entries.length > 1 ? 3 : 0;
  let acc = 0;
  const shown = hover ? entries.find(([k]) => k === hover) : null;
  return (
    <GlowCard as="figure" className="p-4">
      <figcaption className="font-semibold mb-3">{title}</figcaption>
      {total === 0 ? <p className="text-muted text-sm">No evidence uploaded yet.</p> : (
        <div className="flex items-center gap-4">
          <svg viewBox="0 0 140 140" className="w-36 h-36 shrink-0 -rotate-90" role="img" aria-label={`${title}: ${entries.map(([k, v]) => `${k} ${v}`).join(", ")}`}>
            <circle cx="70" cy="70" r={R} fill="none" stroke="var(--color-navy)" strokeWidth="16" />
            {entries.map(([k, v]) => {
              const len = (v / total) * C;
              const seg = (
                <circle key={k} cx="70" cy="70" r={R} fill="none" stroke={MEDIA_COLORS[k]} strokeWidth={hover === k ? 20 : 16}
                  strokeDasharray={`${drawn ? Math.max(0.5, len - GAP) : 0} ${C}`} strokeDashoffset={-acc}
                  className="donut-seg" opacity={hover && hover !== k ? 0.35 : 1}
                  onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
                  <title>{`${k}: ${v} (${Math.round((v / total) * 100)}%)`}</title>
                </circle>
              );
              acc += len;
              return seg;
            })}
            <g className="rotate-90 origin-center">
              <text x="70" y="68" textAnchor="middle" className="fill-[var(--color-text)] font-extrabold" fontSize="24">{shown ? shown[1] : total}</text>
              <text x="70" y="86" textAnchor="middle" className="fill-[var(--color-muted)]" fontSize="10">{shown ? shown[0] : "files"}</text>
            </g>
          </svg>
          <ul className="grid gap-1.5 text-sm grow min-w-0">
            {entries.map(([k, v]) => (
              <li key={k} className={`flex items-center gap-2 rounded px-1 cursor-default ${hover === k ? "bg-panel2" : ""}`} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
                <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: MEDIA_COLORS[k] }} aria-hidden="true" />
                <span className="capitalize text-muted grow truncate">{k}</span>
                <span className="tabular-nums font-semibold">{v}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </GlowCard>
  );
}

/* Indicators by severity – segmented meter with labels (status colours always paired with text). */
const SEV = [["high", "High", "var(--color-bad)"], ["medium", "Medium", "var(--color-warn)"], ["low", "Low", "var(--color-blue)"], ["info", "Info", "var(--color-muted)"]] as const;

export function SeverityMeter({ data }: { data: Record<string, number> }) {
  const total = SEV.reduce((a, [k]) => a + (data[k] ?? 0), 0);
  const [drawn, setDrawn] = useState(reduced());
  useEffect(() => { const t = setTimeout(() => setDrawn(true), 60); return () => clearTimeout(t); }, []);
  return (
    <GlowCard as="figure" className="p-4">
      <figcaption className="font-semibold mb-3">Indicators by severity</figcaption>
      {total === 0 ? <p className="text-muted text-sm">No indicators raised yet.</p> : (
        <>
          <div className="flex h-4 gap-[2px] rounded-full overflow-hidden bg-navy" role="img" aria-label={`Indicators by severity: ${SEV.map(([k, l]) => `${l} ${data[k] ?? 0}`).join(", ")}`}>
            {SEV.filter(([k]) => data[k]).map(([k, l, c]) => (
              <span key={k} className="h-full meter-seg" title={`${l}: ${data[k]}`} style={{ width: drawn ? `${(data[k] / total) * 100}%` : "0%", background: c }} />
            ))}
          </div>
          <ul className="grid grid-cols-2 gap-2 mt-4 text-sm">
            {SEV.map(([k, l, c]) => (
              <li key={k} className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full" style={{ background: c }} aria-hidden="true" /><span className="text-muted grow">{l}</span><span className="tabular-nums font-semibold">{data[k] ?? 0}</span></li>
            ))}
          </ul>
        </>
      )}
    </GlowCard>
  );
}

/* Analysis jobs – pipeline of real job states with an animated flow line. */
export function JobPipeline({ data }: { data: Record<string, number> }) {
  const steps = [["queued", "Queued"], ["running", "Running"], ["completed", "Completed"], ["partial", "Partial"], ["failed", "Failed"]] as const;
  const color: Record<string, string> = { queued: "var(--color-muted)", running: "var(--color-cyan)", completed: "var(--color-ok)", partial: "var(--color-warn)", failed: "var(--color-bad)" };
  const total = Object.values(data).reduce((a, b) => a + b, 0);
  return (
    <GlowCard as="figure" className="p-4">
      <figcaption className="font-semibold mb-3">Analysis pipeline</figcaption>
      {total === 0 ? <p className="text-muted text-sm">No analyses run yet.</p> : (
        <ol className="pipeline" aria-label={`Analysis jobs: ${steps.map(([k, l]) => `${l} ${data[k] ?? 0}`).join(", ")}`}>
          {steps.map(([k, l]) => (
            <li key={k} className={`pipeline-step ${data[k] ? "on" : ""} ${k === "running" && data[k] ? "pulse" : ""}`} style={{ ["--c" as string]: color[k] }}>
              <span className="pipeline-node" aria-hidden="true" />
              <span className="text-muted text-sm grow">{l}</span>
              <span className="tabular-nums font-semibold">{data[k] ?? 0}</span>
            </li>
          ))}
        </ol>
      )}
    </GlowCard>
  );
}

/* Recent activity – timeline with friendly labels and relative time. */
const ACTION: Record<string, [string, string]> = {
  "case.created": ["Investigation created", "var(--color-cyan)"], "case.updated": ["Investigation updated", "var(--color-blue)"],
  "case.deleted": ["Investigation deleted", "var(--color-bad)"], "evidence.uploaded": ["Evidence uploaded", "var(--color-gold)"],
  "evidence.rejected": ["Upload rejected", "var(--color-bad)"], "evidence.verified": ["Integrity verified", "var(--color-ok)"],
  "evidence.accessed": ["Evidence opened", "var(--color-muted)"], "evidence.removed": ["Evidence removed", "var(--color-bad)"],
  "analysis.started": ["Analysis started", "var(--color-cyan)"], "analysis.completed": ["Analysis completed", "var(--color-ok)"],
  "analysis.failed": ["Analysis failed", "var(--color-bad)"], "report.generated": ["Report generated", "var(--color-gold)"],
};

function ago(iso: string) {
  const utc = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z"; // server timestamps are UTC
  const s = (Date.now() - new Date(utc).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return fmtDate(iso);
}

export function ActivityTimeline({ items }: { items: { action: string; case_id: string; created_at: string }[] }) {
  return (
    <ol className="timeline" aria-label="Recent activity">
      {items.slice(0, 10).map((a, i) => {
        const [label, c] = ACTION[a.action] ?? [a.action, "var(--color-muted)"];
        return (
          <li key={i} className="timeline-item rise-in" style={{ animationDelay: `${i * 50}ms`, ["--c" as string]: c }}>
            <span className="timeline-dot" aria-hidden="true" />
            <div className="flex flex-wrap items-baseline gap-x-3">
              {a.case_id ? <Link to={`/app/cases/${a.case_id}`} className="font-medium hover:text-cyan">{label}</Link> : <span className="font-medium">{label}</span>}
              <time className="text-muted text-xs" dateTime={a.created_at} title={fmtDate(a.created_at)}>{ago(a.created_at)}</time>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Remembers a per-viewer preference (falls back silently when storage is unavailable). */
export function usePref<T extends string>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => { try { return (localStorage.getItem(key) as T) || initial; } catch { return initial; } });
  const set = (x: T) => { setV(x); try { localStorage.setItem(key, x); } catch { /* ignore */ } };
  return [v, set];
}
