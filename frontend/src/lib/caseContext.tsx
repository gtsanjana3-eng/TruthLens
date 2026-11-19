import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type CaseItem, type Evidence, type Job, type JobDetail } from "./api";
import { usePolling } from "./hooks";

interface CaseCtx {
  caseItem: CaseItem | null;
  evidence: Evidence[];
  results: JobDetail | null;
  activeJob: Job | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  startAnalysis: () => Promise<void>;
  analysisError: string | null;
}

const Ctx = createContext<CaseCtx | null>(null);

export function CaseProvider({ caseId, children }: { caseId: string; children: ReactNode }) {
  const [caseItem, setCase] = useState<CaseItem | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [results, setResults] = useState<JobDetail | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [c, ev, res, jobs] = await Promise.all([api.getCase(caseId), api.evidence(caseId), api.latest(caseId), api.jobs(caseId)]);
      setCase(c);
      setEvidence(ev);
      setResults(res);
      const running = jobs.find((j) => j.status === "queued" || j.status === "running") ?? null;
      setActiveJob(running);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => { setLoading(true); reload(); }, [reload]);

  usePolling(async () => {
    if (!activeJob) return;
    try {
      const j = await api.job(activeJob.id);
      setActiveJob(j);
      if (j.status !== "queued" && j.status !== "running") {
        setActiveJob(null);
        if (j.status === "failed") setAnalysisError(j.error || "Analysis failed");
        reload();
      }
    } catch { /* keep polling */ }
  }, 1200, !!activeJob);

  const startAnalysis = useCallback(async () => {
    setAnalysisError(null);
    try {
      const j = await api.analyze(caseId);
      setActiveJob(j);
    } catch (e) {
      setAnalysisError((e as Error).message);
    }
  }, [caseId]);

  return <Ctx.Provider value={{ caseItem, evidence, results, activeJob, loading, error, reload, startAnalysis, analysisError }}>{children}</Ctx.Provider>;
}

export function useCase() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useCase outside CaseProvider");
  return c;
}
