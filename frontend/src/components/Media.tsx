import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface PlayerHandle {
  /** Jump to t seconds. `play` = start playback there; `until` = stop automatically at that time (segment playback). */
  seek: (t: number, play?: boolean, until?: number) => void;
  pause: () => void;
  element: () => HTMLMediaElement | null;
}

const fmt = (s: number) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}` : "–");

/** Only one evidence player sounds at a time: starting one pauses every other audio/video on the page. */
let installed = false;
export function installSinglePlayback() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("play", (e) => {
    document.querySelectorAll("audio, video").forEach((m) => { if (m !== e.target) (m as HTMLMediaElement).pause(); });
  }, true);
}

/** Evidence player with an explicit Play/Pause button, time readout and native controls. */
export const MediaPlayer = forwardRef<PlayerHandle, { kind: "audio" | "video"; src: string; label: string }>(function MediaPlayer({ kind, src, label }, ref) {
  const el = useRef<HTMLMediaElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(NaN);
  /** End of the segment being played; playback pauses itself here so a 4-second excerpt never runs on for minutes. */
  const stopAt = useRef<number | null>(null);
  const [segment, setSegment] = useState<[number, number] | null>(null);
  const segStart = useRef(0);
  const clearSegment = () => { stopAt.current = null; setSegment(null); };
  const box = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(true);
  useEffect(installSinglePlayback, []);
  // When playback is started from elsewhere on the page (timestamps, charts) the player may be off-screen:
  // track visibility so a pinned Pause/Stop bar is always reachable while it plays.
  useEffect(() => {
    const node = box.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0.3 });
    io.observe(node);
    return () => io.disconnect();
  }, []);
  // Pause if the player leaves the page (route/tab change) so audio never keeps running unseen.
  useEffect(() => () => { el.current?.pause(); }, []);
  useImperativeHandle(ref, () => ({
    seek: (time: number, play = false, until?: number) => {
      const m = el.current;
      if (!m) return;
      const start = Math.max(0, time);
      m.currentTime = start;
      if (play && until !== undefined && until > start) { stopAt.current = until; segStart.current = start; setSegment([start, until]); } else clearSegment();
      if (play) m.play().catch(() => undefined);
    },
    pause: () => { clearSegment(); el.current?.pause(); },
    element: () => el.current,
  }), []);
  const toggle = () => {
    const m = el.current;
    if (!m) return;
    clearSegment(); // a manual Play continues normally from here
    if (m.paused) m.play().catch(() => undefined); else m.pause();
  };
  const stop = () => { clearSegment(); const m = el.current; if (m) { m.pause(); m.currentTime = 0; } };
  const pause = () => { clearSegment(); el.current?.pause(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === " " && e.target === e.currentTarget) { e.preventDefault(); toggle(); }
  };
  const common = {
    src, controls: true, "aria-label": label, preload: "metadata" as const,
    onPlay: () => setPlaying(true), onPause: () => setPlaying(false), onEnded: () => setPlaying(false),
    onTimeUpdate: (e: React.SyntheticEvent<HTMLMediaElement>) => {
      const m = e.currentTarget;
      setT(m.currentTime);
      if (stopAt.current !== null && m.currentTime >= stopAt.current) { clearSegment(); m.pause(); }
    },
    onSeeking: (e: React.SyntheticEvent<HTMLMediaElement>) => {
      // user dragged the native scrubber outside the segment -> treat as normal playback
      const s = stopAt.current, now = e.currentTarget.currentTime;
      if (s !== null && (now < segStart.current - 0.25 || now > s)) clearSegment();
    },
    onLoadedMetadata: (e: React.SyntheticEvent<HTMLMediaElement>) => setDur(e.currentTarget.duration),
  };
  return (
    <div ref={box} className="grid gap-2" tabIndex={-1} onKeyDown={onKey}>
      {kind === "video"
        ? <video ref={(n) => { el.current = n; }} className="w-full rounded-lg bg-navy" {...common} />
        : <audio ref={(n) => { el.current = n; }} className="w-full" {...common} />}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={`btn ${playing ? "btn-gold" : "btn-primary"} min-w-28`} onClick={toggle} aria-pressed={playing}>
          {playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <button type="button" className="btn btn-ghost" onClick={stop}>■ Stop</button>
        <span className="mono text-sm" aria-live="off">{fmt(t)} / {fmt(dur)}</span>
        {segment && playing && <span className="chip text-gold text-xs">Playing segment {segment[0].toFixed(1)}–{segment[1].toFixed(1)}s · stops automatically</span>}
      </div>
      {playing && !inView && typeof document !== "undefined" && createPortal(
        <div role="region" aria-label={`Now playing: ${label}`}
          style={{ position: "fixed", bottom: 16, left: "50%", transform: "translateX(-50%)", zIndex: 60 }}
          className="rounded-xl border border-line bg-panel shadow-xl px-3 py-2 flex items-center gap-2 max-w-[calc(100vw-32px)]">
          <span className="text-sm truncate max-w-40 sm:max-w-64">🔊 {label}</span>
          <span className="mono text-xs text-muted">{fmt(t)} / {fmt(dur)}</span>
          <button type="button" className="btn btn-gold" onClick={pause}>❚❚ Pause</button>
          <button type="button" className="btn btn-ghost" onClick={stop}>■ Stop</button>
        </div>,
        document.body,
      )}
    </div>
  );
});

/** Scroll an element into view and briefly highlight it so the user sees where "Inspect" took them. */
export function flash(node: Element | null) {
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("inspect-flash");
  window.setTimeout(() => node.classList.remove("inspect-flash"), 1600);
}
