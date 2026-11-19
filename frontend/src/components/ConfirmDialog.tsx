import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * In-app confirmation dialog. Replaces window.confirm(), which embedded browsers (and some
 * privacy settings) block – it then returns false instantly and the action silently does nothing.
 */
interface Options { title: string; message: ReactNode; confirmLabel: string; danger?: boolean }

export function useConfirm(): [(o: Options) => Promise<boolean>, ReactNode] {
  const [opts, setOpts] = useState<Options | null>(null);
  const resolver = useRef<(v: boolean) => void>();
  const ask = useCallback((o: Options) => new Promise<boolean>((resolve) => { resolver.current = resolve; setOpts(o); }), []);
  const close = useCallback((v: boolean) => { resolver.current?.(v); resolver.current = undefined; setOpts(null); }, []);
  return [ask, opts ? <ConfirmDialog {...opts} onClose={close} /> : null];
}

function ConfirmDialog({ title, message, confirmLabel, danger, onClose }: Options & { onClose: (v: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  // Portal to <body>: animated page containers use transforms, which would trap position:fixed.
  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center p-4 bg-ink/75 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(false); }}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="cd-title" aria-describedby="cd-msg" className="card w-full max-w-md p-5 rise-in">
        <h2 id="cd-title" className="text-lg font-semibold">{title}</h2>
        <div id="cd-msg" className="text-sm text-muted mt-2">{message}</div>
        <div className="flex justify-end gap-2 mt-5">
          <button ref={cancelRef} type="button" className="btn btn-ghost" onClick={() => onClose(false)}>Cancel</button>
          <button type="button" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={() => onClose(true)}>{confirmLabel}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
