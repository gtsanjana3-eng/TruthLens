import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { TimelineStrip } from "../components/Charts";
import { AIDetectionPanel } from "../components/AIDetection";
import { AgentPanel, EvidencePicker, IndicatorCard, fmtLocation } from "../components/Forensics";
import { MediaPlayer, flash, type PlayerHandle } from "../components/Media";
import { EmptyState, ErrorState, Spinner } from "../components/ui";
import { api, fmtTime, type AgentResult, type Evidence, type Indicator, type MediaType } from "../lib/api";
import { useCase } from "../lib/caseContext";
import { useAsync, useAuthedUrl } from "../lib/hooks";

function useWorkspace(media: MediaType) {
  const { evidence, results } = useCase();
  const [sp, setSp] = useSearchParams();
  const items = evidence.filter((e) => e.media_type === media);
  const selectedId = sp.get("e") && items.some((i) => i.id === sp.get("e")) ? sp.get("e") : items[0]?.id ?? null;
  const ev = items.find((i) => i.id === selectedId) ?? null;
  const agentResults = results?.agent_results.filter((r) => r.evidence_id === selectedId) ?? [];
  const indicators = results?.indicators.filter((i) => i.evidence_id === selectedId) ?? [];
  const findings = results?.findings.filter((f) => selectedId && f.evidence_ids.includes(selectedId)) ?? [];
  return { items, ev, agentResults, indicators, findings, select: (id: string) => setSp({ e: id }), hasResults: !!results };
}

function NoEvidence({ media }: { media: MediaType }) {
  return <EmptyState title={`No ${media} evidence in this case`} action={<Link className="btn btn-primary" to="../upload">Upload {media} evidence</Link>}>The {media} analysis workspace opens once a {media} file is attached.</EmptyState>;
}

function NotAnalysed() {
  return <p className="card p-4 text-sm text-muted">This item has not been analysed yet. Use <strong className="text-text">Run multi-agent analysis</strong> above.</p>;
}

function Findings({ findings }: { findings: ReturnType<typeof useWorkspace>["findings"] }) {
  if (!findings.length) return null;
  return (
    <section className="card p-4" aria-labelledby="xf-h">
      <h3 id="xf-h" className="font-semibold mb-2">Cross-modal findings involving this item</h3>
      <ul className="grid gap-2 text-sm">{findings.map((f) => <li key={f.id}><span className={`chip mr-2 ${f.effect === "strengthens" ? "text-warn" : f.effect === "weakens" ? "text-ok" : "text-muted"}`}>{f.effect === "strengthens" ? "↑ raises concern" : f.effect === "weakens" ? "↓ lowers concern" : "· context"}</span>{f.description}</li>)}</ul>
    </section>
  );
}

function IndicatorList({ indicators, ev, onSelect }: { indicators: Indicator[]; ev: Evidence; onSelect?: (i: Indicator) => void }) {
  if (!indicators.length) return <p className="card p-4 text-sm text-muted">No manipulation indicators were raised for {ev.label}. Absence of indicators does not establish authenticity.</p>;
  return <ul className="grid gap-3" aria-label="Manipulation indicators">{indicators.map((i) => <IndicatorCard key={i.id} ind={i} onSelect={onSelect ? () => onSelect(i) : undefined} />)}</ul>;
}

/* ---------------- Image ---------------- */
export function ImageWorkspace() {
  const w = useWorkspace("image");
  const [layer, setLayer] = useState<"original" | "heatmap" | "ela">("original");
  const [focus, setFocus] = useState<number | null>(null);
  const viewerRef = useRef<HTMLElement>(null);
  const img = w.agentResults.find((r) => r.agent_name === "Image Forensics Agent");
  const src = layer === "original" ? (w.ev ? `/api/evidence/${w.ev.id}/file?inline=true` : null)
    : img ? `/api/evidence/${w.ev!.id}/artifacts/${img.artifacts[layer === "heatmap" ? "heatmap" : "ela"]}` : null;
  const { url, failed } = useAuthedUrl(src);
  if (!w.ev) return <NoEvidence media="image" />;
  const [aw, ah] = (img?.artifacts.analysis_size as [number, number]) ?? [0, 0];
  const regions = w.indicators.filter((i) => i.category === "region" && i.location.x !== undefined);
  const localized = !!img?.artifacts.localized;
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <EvidencePicker items={w.items} value={w.ev.id} onChange={w.select} />
      <AIDetectionPanel title="Image forensics – AI-generated image detection" result={w.agentResults.find((r) => r.agent_name === "Image Forensics Agent (AI model)")} evidenceId={w.ev.id} />
      <div className="grid xl:grid-cols-[1.4fr_1fr] gap-4">
        <section ref={viewerRef} className="card p-4 min-w-0" aria-labelledby="iv-h">
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h2 id="iv-h" className="font-semibold mr-auto">{w.ev.label} · {w.ev.original_filename}</h2>
            <div role="radiogroup" aria-label="Image layer" className="flex gap-1">
              {(["original", "heatmap", "ela"] as const).map((l) => (
                <button key={l} role="radio" aria-checked={layer === l} disabled={l !== "original" && (!img || (l === "heatmap" && !localized))}
                  className={`btn text-xs ${layer === l ? "btn-primary" : "btn-ghost"}`} onClick={() => setLayer(l)}>
                  {l === "original" ? "Original" : l === "heatmap" ? "Suspicion heatmap" : "ELA map"}
                </button>))}
            </div>
          </div>
          <div className="relative bg-navy rounded-lg overflow-hidden min-h-40 grid place-items-center">
            {failed ? <p className="text-bad text-sm p-6">Image could not be loaded.</p> : !url ? <Spinner label="Loading image" /> : (
              <div className="relative max-w-full">
                <img src={url} alt={`${w.ev.label} ${layer === "original" ? "original evidence" : layer === "heatmap" ? "suspicion heatmap from measured ELA, noise and copy-move signals" : "error level analysis map"}`} className="max-h-[560px] max-w-full h-auto w-auto" />
                {aw > 0 && regions.map((r) => (
                  <div key={r.id} aria-hidden="true" className={`absolute ${focus === r.id ? "border-4 border-gold bg-gold/20 inspect-flash" : "border-2 border-cyan/80"}`}
                    style={{ left: `${(r.location.x / aw) * 100}%`, top: `${(r.location.y / ah) * 100}%`, width: `${(r.location.w / aw) * 100}%`, height: `${(r.location.h / ah) * 100}%` }}>
                    <span className="absolute -top-5 left-0 text-[10px] bg-ink/80 px-1 rounded">#{r.id}</span>
                  </div>))}
              </div>
            )}
          </div>
          {img && !localized && <p className="text-xs text-muted mt-2">No localized indicators were measured, so no suspicion heatmap is shown (heatmaps are never fabricated).</p>}
          {focus !== null && !regions.some((r) => r.id === focus) && <p role="status" className="text-xs text-gold mt-2">This finding applies to the whole image (no specific region).</p>}
          {img && localized && <p className="text-xs text-muted mt-2">Heatmap basis: {img.artifacts.heatmap_basis}. Boxes mark indicator regions (#id matches the list).</p>}
        </section>
        <section aria-labelledby="ii-h" className="grid gap-3 content-start min-w-0">
          <h2 id="ii-h" className="font-semibold">Flagged regions & metadata observations</h2>
          {w.hasResults ? <IndicatorList indicators={w.indicators} ev={w.ev} onSelect={(i) => { setFocus(i.id); setLayer("original"); flash(viewerRef.current); }} /> : <NotAnalysed />}
        </section>
      </div>
      <Findings findings={w.findings} />
      <MetadataDetective results={w.agentResults} />
      {w.agentResults.map((r) => <AgentPanel key={r.id} r={r} />)}
    </div>
  );
}

export function MetadataDetective({ results }: { results: AgentResult[] }) {
  const md = results.find((r) => r.agent_name === "Metadata Verification Agent");
  if (!md) return null;
  const meta = (md.artifacts.metadata ?? {}) as Record<string, unknown>;
  const entries = Object.entries(meta).filter(([k]) => !["generator_hits"].includes(k));
  return (
    <section className="card p-4" aria-labelledby="md-h">
      <h3 id="md-h" className="font-semibold">Metadata detective</h3>
      <p className="text-xs text-muted mb-3">Metadata can be stripped or forged; it is never treated as proof on its own.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs"><caption className="sr-only">File metadata</caption>
          <tbody>{entries.map(([k, v]) => <tr key={k} className="border-t border-line/40"><th scope="row" className="text-left text-muted py-1 pr-4 font-medium align-top">{k}</th><td className="mono break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

/* ---------------- Video ---------------- */
interface Frame { frame: number; t: number; diff: number; anomaly: number; thumb: string | null; flags: string[]; face: number[] | null; mouth: number | null }

export function VideoWorkspace() {
  const w = useWorkspace("video");
  const va = w.agentResults.find((r) => r.agent_name === "Video Analysis Agent");
  const sync = w.agentResults.find((r) => r.agent_name === "Video Analysis Agent (voice-face sync)");
  const voiceTrack = w.agentResults.find((r) => r.agent_name === "Audio Analysis Agent (embedded track · AI voice model)");
  const frames: Frame[] = useMemo(() => (va?.artifacts.frames ?? []) as Frame[], [va]);
  const [idx, setIdx] = useState(0);
  const playerRef = useRef<PlayerHandle>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const { url: videoUrl, failed } = useAuthedUrl(w.ev ? `/api/evidence/${w.ev.id}/file?inline=true` : null);
  useEffect(() => setIdx(0), [w.ev?.id]);
  const thumbIdx = useMemo(() => { for (let i = idx; i >= 0; i--) if (frames[i]?.thumb) return i; return frames.findIndex((f) => f.thumb); }, [idx, frames]);
  const { url: thumbUrl } = useAuthedUrl(w.ev && thumbIdx >= 0 && frames[thumbIdx]?.thumb ? `/api/evidence/${w.ev.id}/artifacts/${frames[thumbIdx].thumb}` : null);
  const flaggedIdx = frames.map((f, i) => (f.flags.length ? i : -1)).filter((i) => i >= 0);
  const go = useCallback((i: number) => {
    const n = Math.max(0, Math.min(frames.length - 1, i));
    setIdx(n);
    if (frames[n]) playerRef.current?.seek(frames[n].t);
  }, [frames]);
  const jumpToTime = (t: number) => { const i = frames.findIndex((f) => f.t >= t); go(i < 0 ? frames.length - 1 : i); };
  /** Inspect: stop playback on the flagged moment and bring the frame inspector into view. */
  const inspectAt = (t: number) => { playerRef.current?.pause(); jumpToTime(t); playerRef.current?.seek(t); flash(inspectorRef.current); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") { e.preventDefault(); go(idx + 1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); go(idx - 1); }
  };
  if (!w.ev) return <NoEvidence media="video" />;
  const f = frames[idx];
  const windows = (sync?.artifacts.windows ?? []) as { t_start: number; t_end: number; corr: number | null }[];
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <EvidencePicker items={w.items} value={w.ev.id} onChange={w.select} />
      <AIDetectionPanel title="Video deepfake detection – face-swap model" evidenceId={w.ev.id} result={w.agentResults.find((r) => r.agent_name === "Video Analysis Agent (AI model)")} onSeek={inspectAt} />
      <AIDetectionPanel title="AI voice detection – the video's soundtrack" result={voiceTrack} onSeek={(t, end) => playerRef.current?.seek(t, true, end ?? t + 4)} />
      <div className="grid xl:grid-cols-2 gap-4">
        <section className="card p-4 min-w-0" aria-labelledby="vp-h">
          <h2 id="vp-h" className="font-semibold mb-3">{w.ev.label} · original playback</h2>
          {failed ? <ErrorState message="Video could not be loaded" /> : videoUrl ? <MediaPlayer ref={playerRef} kind="video" src={videoUrl} label={`Original video evidence ${w.ev.label}`} /> : <Spinner label="Loading video" />}
        </section>
        <section ref={inspectorRef} className="card p-4 min-w-0" aria-labelledby="fi-h">
          <h2 id="fi-h" className="font-semibold mb-3">Frame-by-frame inspector</h2>
          {!va ? <NotAnalysed /> : va.status !== "completed" ? <ErrorState message={va.errors.join("; ") || "Video analysis did not complete"} /> : (
            <div tabIndex={0} onKeyDown={onKey} aria-label="Frame inspector. Use left and right arrow keys to step through sampled frames." className="grid gap-3">
              <div className="bg-navy rounded-lg grid place-items-center min-h-40">
                {thumbUrl ? <img src={thumbUrl} alt={`Sampled frame ${frames[thumbIdx]?.frame} at ${frames[thumbIdx]?.t.toFixed(2)} seconds`} className="max-h-72" /> : <Spinner label="Loading frame" />}
              </div>
              <p className="text-sm" aria-live="polite">Frame <span className="mono">{f?.frame}</span> · t = <span className="mono">{f ? fmtTime(f.t) : "—"}</span> · temporal anomaly <span className="mono">{f?.anomaly.toFixed(2)}</span>
                {f?.face ? " · face detected" : " · no face"}{f?.flags.length ? <> · <strong className="text-gold">flagged: {f.flags.join(", ").replaceAll("_", " ")}</strong></> : null}</p>
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-ghost text-xs" onClick={() => go(0)}>⏮ First</button>
                <button className="btn btn-ghost text-xs" onClick={() => go(idx - 1)}>◀ Prev</button>
                <button className="btn btn-ghost text-xs" onClick={() => go(idx + 1)}>Next ▶</button>
                <button className="btn btn-ghost text-xs" disabled={!flaggedIdx.length} onClick={() => go(flaggedIdx.find((i) => i > idx) ?? flaggedIdx[0])}>Next flagged ⚑</button>
                <label className="sr-only" htmlFor="frame-slider">Frame position</label>
                <input id="frame-slider" type="range" min={0} max={frames.length - 1} value={idx} onChange={(e) => go(Number(e.target.value))} className="grow accent-cyan" />
              </div>
            </div>
          )}
        </section>
      </div>
      {frames.length > 0 && (
        <section className="card p-4">
          <h2 className="font-semibold mb-2">Suspicion timeline (measured temporal anomaly per sampled frame)</h2>
          <TimelineStrip values={frames.map((x) => x.anomaly)} times={frames.map((x) => x.t)} flagged={frames.map((x) => x.flags.length > 0)} selected={idx} onSelect={go}
            label={`Temporal anomaly for ${w.ev.label}; basis: ${va?.artifacts.heatmap_basis ?? ""}`} />
        </section>
      )}
      <section className="card p-4" aria-labelledby="sync-h">
        <h2 id="sync-h" className="font-semibold">Voice-face synchronization analyzer</h2>
        {!sync ? <p className="text-sm text-muted mt-2">{w.hasResults ? "Not run." : "Run the analysis to compare mouth motion with speech."}</p> : (
          <>
            <p className="text-sm mt-2">{sync.interpretation}</p>
            {sync.status === "completed" && <p className="text-xs text-muted mt-1">Global correlation {sync.artifacts.global_corr} · best audio offset {sync.artifacts.best_lag_s}s</p>}
            {windows.length > 0 && (
              <ul className="grid sm:grid-cols-2 lg:grid-cols-4 gap-2 mt-3 text-xs">
                {windows.map((wd, i) => (
                  <li key={i}><button className={`btn w-full text-xs ${wd.corr !== null && wd.corr < 0 ? "btn-gold" : "btn-ghost"}`} onClick={() => inspectAt(wd.t_start)}>
                    {wd.corr !== null && wd.corr < 0 ? "⚑ " : ""}{wd.t_start.toFixed(1)}–{wd.t_end.toFixed(1)}s · r={wd.corr ?? "n/a"}</button></li>))}
              </ul>
            )}
            <ul className="list-disc pl-4 text-xs text-muted mt-3">{sync.limitations.slice(0, 3).map((l) => <li key={l}>{l}</li>)}</ul>
          </>
        )}
      </section>
      <section aria-labelledby="vi-h" className="grid gap-3">
        <h2 id="vi-h" className="font-semibold">Flagged frames, segments & observations</h2>
        {w.hasResults ? <IndicatorList indicators={w.indicators} ev={w.ev} onSelect={(i) => inspectAt(i.location.t_start ?? 0)} /> : <NotAnalysed />}
      </section>
      <Findings findings={w.findings} />
      <MetadataDetective results={w.agentResults} />
      {w.agentResults.map((r) => <AgentPanel key={r.id} r={r} />)}
    </div>
  );
}

/* ---------------- Audio ---------------- */
export function AudioWorkspace() {
  const w = useWorkspace("audio");
  const aa = w.agentResults.find((r) => r.agent_name === "Audio Analysis Agent");
  const voice = w.agentResults.find((r) => r.agent_name === "Audio Analysis Agent (AI model)");
  const playerRef = useRef<PlayerHandle>(null);
  const playerSection = useRef<HTMLElement>(null);
  const { url, failed } = useAuthedUrl(w.ev ? `/api/evidence/${w.ev.id}/file?inline=true` : null);
  const { url: spec } = useAuthedUrl(w.ev && aa?.artifacts.spectrogram ? `/api/evidence/${w.ev.id}/artifacts/${aa.artifacts.spectrogram}` : null);
  if (!w.ev) return <NoEvidence media="audio" />;
  /** Play only the requested excerpt [t, end] and stop there; Pause / Stop also stop it at any time. */
  const seek = (t: number, end?: number) => playerRef.current?.seek(Math.max(0, t), true, end ?? t + 5);
  const inspect = (i: Indicator) => {
    const s = i.location.t_start ?? 0;
    seek(s - 0.5, (i.location.t_end ?? s + 3) + 0.5);
    flash(playerSection.current);
  };
  const tl = aa?.artifacts.timeline as { values: number[]; basis: string } | undefined;
  const wf = aa?.artifacts.waveform as { t: number[]; rms: number[] } | undefined;
  const maxRms = wf ? Math.max(...wf.rms, 1e-6) : 1;
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      <EvidencePicker items={w.items} value={w.ev.id} onChange={w.select} />
      <AIDetectionPanel title="AI voice detection – synthetic / cloned speech" result={voice} onSeek={seek} />
      <section ref={playerSection} className="card p-4 grid gap-3" aria-labelledby="ap-h">
        <h2 id="ap-h" className="font-semibold">{w.ev.label} · {w.ev.original_filename}</h2>
        {failed ? <ErrorState message="Audio could not be loaded" /> : url ? <MediaPlayer ref={playerRef} kind="audio" src={url} label={`Original audio evidence ${w.ev.label}`} /> : <Spinner label="Loading audio" />}
        {wf && <TimelineStrip values={wf.rms.map((v) => v / maxRms)} times={wf.t} label="Loudness envelope (RMS, normalised) – click to play 5 s from that point" onSelect={(i) => seek(wf.t[i])} />}
        {tl && <TimelineStrip values={tl.values} times={tl.values.map((_, i) => i)} flagged={tl.values.map((v) => v > 0)} label={`Per-second anomaly timeline – ${tl.basis}`} onSelect={(i) => seek(Math.max(0, i - 1), i + 2)} />}
        {spec && <figure><img src={spec} alt={`Spectrogram of ${w.ev.label} (first two minutes): frequency over time`} className="w-full rounded-lg" /><figcaption className="text-xs text-muted mt-1">Spectrogram (visualisation of the signal, not a suspicion map)</figcaption></figure>}
      </section>
      <section aria-labelledby="aseg-h" className="grid gap-3">
        <h2 id="aseg-h" className="font-semibold">Flagged audio segments</h2>
        {w.hasResults ? <IndicatorList indicators={w.indicators} ev={w.ev} onSelect={inspect} /> : <NotAnalysed />}
      </section>
      <Findings findings={w.findings} />
      <MetadataDetective results={w.agentResults} />
      {w.agentResults.map((r) => <AgentPanel key={r.id} r={r} />)}
    </div>
  );
}

/* ---------------- Text ---------------- */
export function TextWorkspace() {
  const w = useWorkspace("text");
  const { results, caseItem } = useCase();
  const content = useAsync(async () => (w.ev ? (await api.blob(`/api/evidence/${w.ev.id}/file`)).text() : ""), [w.ev?.id]);
  const ta = w.agentResults.find((r) => r.agent_name === "Text Analysis Agent");
  const aiText = w.agentResults.find((r) => r.agent_name === "Text Analysis Agent (AI model)");
  const ctx = results?.agent_results.find((r) => r.agent_name.includes("context statement"));
  const claims = (ta?.artifacts.claims ?? null) as Record<string, any> | null;
  const cues = (ta?.artifacts.cues ?? []) as { start: number; end: number; text: string }[];
  const [hl, setHl] = useState<{ start: number; end: number } | null>(null);
  const textRef = useRef<HTMLPreElement>(null);
  const highlight = (i: Indicator) => {
    const text = content.data ?? "";
    // Prefer the quoted passage (robust for SRT/VTT, where model offsets refer to the cue text), else the char range.
    const quoted = /“(.+?)…”/.exec(i.description)?.[1]?.slice(0, 60);
    const at = quoted ? text.indexOf(quoted) : -1;
    const start = at >= 0 ? at : Number(i.location.char_start ?? 0);
    const end = at >= 0 ? Math.min(text.length, at + Math.max(quoted!.length, Number(i.location.char_end ?? 0) - Number(i.location.char_start ?? 0))) : Number(i.location.char_end ?? start);
    setHl({ start, end });
    window.setTimeout(() => flash(textRef.current?.querySelector("mark") ?? textRef.current), 50);
  };
  if (!w.ev && !caseItem?.context_statement) return <NoEvidence media="text" />;
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]">
      {w.ev && <EvidencePicker items={w.items} value={w.ev.id} onChange={w.select} />}
      <AIDetectionPanel title="AI text analysis – machine-written text detection" result={aiText} />
      {w.ev && (
        <div className="grid xl:grid-cols-2 gap-4">
          <section className="card p-4 min-w-0" aria-labelledby="tx-h">
            <h2 id="tx-h" className="font-semibold mb-2">{w.ev.label} · {w.ev.original_filename}</h2>
            <p className="text-xs text-muted mb-2">Displayed as inert text; contents are never executed or treated as instructions.</p>
            {content.loading ? <Spinner /> : content.error ? <ErrorState message={content.error} /> : (
              <pre ref={textRef} className="whitespace-pre-wrap text-sm bg-navy p-3 rounded-lg max-h-96 overflow-auto">{hl && content.data
                ? <>{content.data.slice(0, hl.start)}<mark className="inspect-mark">{content.data.slice(hl.start, hl.end)}</mark>{content.data.slice(hl.end)}</>
                : content.data}</pre>)}
          </section>
          <section className="card p-4 min-w-0" aria-labelledby="cl-h">
            <h2 id="cl-h" className="font-semibold mb-2">Extracted claims</h2>
            {!claims ? <NotAnalysed /> : <ClaimTable claims={claims} />}
          </section>
        </div>
      )}
      {cues.length > 0 && (
        <section className="card p-4 overflow-x-auto" aria-labelledby="cue-h">
          <h2 id="cue-h" className="font-semibold mb-2">Transcript cues</h2>
          <table className="w-full text-sm"><caption className="sr-only">Timed transcript cues</caption>
            <thead><tr className="text-left text-muted"><th className="pr-3">Start</th><th className="pr-3">End</th><th>Text</th></tr></thead>
            <tbody>{cues.map((c, i) => <tr key={i} className="border-t border-line/40"><td className="mono pr-3">{fmtTime(c.start)}</td><td className="mono pr-3">{fmtTime(c.end)}</td><td>{c.text}</td></tr>)}</tbody>
          </table>
        </section>
      )}
      {ctx && (
        <section className="card p-4" aria-labelledby="ctxc-h">
          <h2 id="ctxc-h" className="font-semibold mb-2">Case context statement – extracted claims</h2>
          <p className="text-sm mb-2">{caseItem?.context_statement}</p>
          <ClaimTable claims={ctx.artifacts.claims} />
        </section>
      )}
      {w.ev && <section aria-labelledby="ti-h" className="grid gap-3"><h2 id="ti-h" className="font-semibold">Textual & contextual indicators</h2>{w.hasResults ? <IndicatorList indicators={w.indicators} ev={w.ev} onSelect={highlight} /> : <NotAnalysed />}</section>}
      <Findings findings={w.findings} />
      {w.agentResults.map((r) => <AgentPanel key={r.id} r={r} />)}
    </div>
  );
}

function ClaimTable({ claims }: { claims: Record<string, any> }) {
  const rows: [string, string][] = [
    ["Dates", (claims.dates ?? []).map((d: any) => `${d.date}${d.ambiguous ? " (ambiguous)" : ""}`).join(", ")],
    ["Times", (claims.times ?? []).join(", ")],
    ["Durations", (claims.durations ?? []).map((d: any) => d.text).join(", ")],
    ["Locations", (claims.locations ?? []).join(", ")],
    ["Devices", (claims.devices ?? []).join(", ")],
    ["Time of day", claims.time_of_day ?? ""],
    ["Speakers", (claims.speakers ?? []).join(", ")],
    ["Persuasive wording", (claims.sensational_terms ?? []).join(", ")],
    ["Claims 'not edited'", claims.negated_media_claims ? "yes" : ""],
  ];
  return (
    <table className="w-full text-sm"><caption className="sr-only">Extracted claims</caption>
      <tbody>{rows.map(([k, v]) => <tr key={k} className="border-t border-line/40"><th scope="row" className="text-left text-muted pr-3 py-1 font-medium">{k}</th><td>{v || <span className="text-muted">none found</span>}</td></tr>)}</tbody>
    </table>
  );
}

export { fmtLocation };
