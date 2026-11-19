import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Load async data with loading/error state and a reload function. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => { if (id === seq.current) setData(d); })
      .catch((e: Error) => { if (id === seq.current) setError(e.message); })
      .finally(() => { if (id === seq.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { run(); }, [run]);
  return { data, error, loading, reload: run, setData };
}

/** Fetch an authenticated binary resource and expose it as an object URL. */
export function useAuthedUrl(path: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!path) { setUrl(null); return; }
    let revoked = false;
    let current: string | null = null;
    setFailed(false);
    api.blob(path).then((b) => {
      if (revoked) return;
      current = URL.createObjectURL(b);
      setUrl(current);
    }).catch(() => !revoked && setFailed(true));
    return () => { revoked = true; if (current) URL.revokeObjectURL(current); };
  }, [path]);
  return { url, failed };
}

/** Poll a function every `ms` while `active` is true. */
export function usePolling(fn: () => void, ms: number, active: boolean) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => saved.current(), ms);
    return () => clearInterval(t);
  }, [ms, active]);
}
