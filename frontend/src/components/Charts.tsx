// Lightweight accessible SVG charts. Every chart has a text alternative (title/desc + data table option).
import { useState } from "react";

export function BarList({ title, data, colorFor }: { title: string; data: Record<string, number>; colorFor?: (k: string) => string }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  const summary = entries.map(([k, v]) => `${k}: ${v}`).join(", ") || "no data";
  return (
    <figure className="card p-4">
      <figcaption className="font-semibold mb-3">{title}</figcaption>
      {entries.length === 0 ? <p className="text-muted text-sm">No data yet.</p> : (
        <ul className="grid gap-2" aria-label={`${title}: ${summary}`}>
          {entries.map(([k, v]) => (
            <li key={k} className="grid grid-cols-[110px_1fr_40px] items-center gap-2 text-sm">
              <span className="text-muted capitalize truncate">{k.replace("_", " ")}</span>
              <span className="h-2.5 rounded bg-navy overflow-hidden" aria-hidden="true"><span className="block h-full rounded" style={{ width: `${(v / max) * 100}%`, background: colorFor?.(k) ?? "var(--color-cyan)" }} /></span>
              <span className="mono text-right">{v}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

/** Time-series strip (e.g. per-frame anomaly or per-second audio anomaly). Clickable points. */
export function TimelineStrip({ values, times, label, onSelect, selected, flagged }: {
  values: number[]; times: number[]; label: string; onSelect?: (i: number) => void; selected?: number; flagged?: boolean[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 800, H = 90;
  const n = values.length;
  if (!n) return <p className="text-muted text-sm">No timeline data.</p>;
  const x = (i: number) => (n === 1 ? W / 2 : (i / (n - 1)) * W);
  const y = (v: number) => H - 6 - Math.max(0, Math.min(1, v)) * (H - 16);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const nFlag = flagged?.filter(Boolean).length ?? 0;
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-24 bg-navy rounded-lg" role="img" aria-label={`${label}. ${n} points from ${times[0]?.toFixed(1)}s to ${times[n - 1]?.toFixed(1)}s, peak ${Math.max(...values).toFixed(2)}${flagged ? `, ${nFlag} flagged` : ""}.`}
        onMouseLeave={() => setHover(null)}>
        <path d={`${path} L${W},${H} L0,${H} Z`} fill="rgba(34,211,238,.12)" />
        <path d={path} fill="none" stroke="var(--color-cyan)" strokeWidth="1.5" />
        {values.map((v, i) => (
          <g key={i}>
            {flagged?.[i] && <path d={`M${x(i)},${y(v) - 9} l5,8 h-10 z`} fill="var(--color-gold)" />}
            {onSelect && <rect x={x(i) - W / n / 2} y={0} width={Math.max(2, W / n)} height={H} fill="transparent" onMouseEnter={() => setHover(i)} onClick={() => onSelect(i)} style={{ cursor: "pointer" }} />}
          </g>
        ))}
        {selected !== undefined && <line x1={x(selected)} x2={x(selected)} y1={0} y2={H} stroke="var(--color-gold)" strokeWidth="2" />}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke="var(--color-muted)" strokeDasharray="3 3" />}
      </svg>
      <figcaption className="text-xs text-muted mt-1 flex justify-between"><span>{label}{hover !== null && ` · t=${times[hover].toFixed(2)}s value=${values[hover].toFixed(2)}`}</span><span>▲ = flagged</span></figcaption>
    </figure>
  );
}
