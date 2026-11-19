import { useState } from "react";
import { Link } from "react-router-dom";
import { ActivityTimeline, CommandBanner, DonutChart, GlowCard, JobPipeline, SeverityMeter, StatTile, usePref } from "../components/DashboardViz";
import { ConcernBadge, EmptyState, ErrorState, LoadingBlock, StatusBadge } from "../components/ui";
import { api, fmtDate } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/hooks";

const CONCERN_EDGE: Record<string, string> = {
  "elevated concern": "var(--color-bad)", "moderate concern": "var(--color-warn)", "low concern": "var(--color-blue)", "no notable indicators": "var(--color-ok)",
};
const sum = (o?: Record<string, number>) => Object.values(o ?? {}).reduce((a, b) => a + b, 0);

export function Dashboard() {
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = usePref<"cards" | "table">("tl.caseView", "cards");
  const stats = useAsync(() => api.stats(), []);
  const cases = useAsync(() => api.cases({ q, status, page, page_size: 9 }), [q, status, page]);
  const recent = useAsync(() => api.cases({ page: 1, page_size: 12 }), []);
  const d = stats.data;
  const pages = cases.data ? Math.max(1, Math.ceil(cases.data.total / cases.data.page_size)) : 1;
  const activeJobs = (d?.jobs_by_status.running ?? 0) + (d?.jobs_by_status.queued ?? 0);

  return (
    <div>
      <h1 className="sr-only">Investigation dashboard</h1>
      <CommandBanner
        name={user?.full_name ?? "investigator"}
        openCases={d?.cases_by_status.open ?? 0}
        activeJobs={activeJobs}
        blips={(recent.data?.items ?? []).map((c) => ({ id: c.id, label: c.title, concern: c.overall_concern }))}
        action={<><Link className="btn btn-primary" to="/app/cases/new">+ New investigation</Link><Link className="btn btn-ghost" to="/app/history">Investigation history</Link></>}
      />

      {stats.error ? <ErrorState message={stats.error} onRetry={stats.reload} /> : (
        <section aria-label="Key figures" className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <StatTile label="Cases" icon="cases" value={sum(d?.cases_by_status)} loading={stats.loading}
            sub={`${d?.cases_by_status.open ?? 0} open · ${d?.cases_by_status.closed ?? 0} closed`} />
          <StatTile label="Evidence items" icon="evidence" value={sum(d?.evidence_by_type)} loading={stats.loading} accent="var(--color-gold)" sub="hashed & write-protected" delay={70} />
          <StatTile label="Indicators raised" icon="indicators" value={sum(d?.indicators_by_severity)} loading={stats.loading} accent="var(--color-warn)"
            sub={`${d?.indicators_by_severity.high ?? 0} high severity`} delay={140} />
          <StatTile label="Analyses run" icon="analyses" value={sum(d?.jobs_by_status)} loading={stats.loading} accent="var(--color-ok)"
            sub={`${d?.jobs_by_status.completed ?? 0} completed`} delay={210} />
        </section>
      )}

      {d && (
        <section className="grid md:grid-cols-3 gap-4 mb-6" aria-label="Charts">
          <DonutChart title="Evidence by media type" data={d.evidence_by_type} />
          <SeverityMeter data={d.indicators_by_severity} />
          <JobPipeline data={d.jobs_by_status} />
        </section>
      )}

      <div className="grid xl:grid-cols-[minmax(0,1fr)_320px] gap-6 items-start">
        <section aria-labelledby="cases-h" className="card p-4 min-w-0">
          <div className="flex flex-wrap items-end gap-3 mb-4">
            <h2 id="cases-h" className="text-lg font-semibold mr-auto">Cases</h2>
            <div className="relative">
              <label htmlFor="case-q" className="label">Search</label>
              <input id="case-q" className="input" style={{ paddingLeft: "2rem" }} placeholder="Title or reference" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
              <svg viewBox="0 0 24 24" className="w-4 h-4 absolute left-2.5 bottom-3 text-muted" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
            </div>
            <div>
              <label htmlFor="case-status" className="label">Status</label>
              <select id="case-status" className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
                <option value="">All</option><option value="open">Open</option><option value="in_review">In review</option><option value="closed">Closed</option>
              </select>
            </div>
            <div className="view-toggle flex rounded-lg border border-line overflow-hidden" role="group" aria-label="Case view">
              <button type="button" className="px-3 py-2 text-xs font-semibold" aria-pressed={view === "cards"} onClick={() => setView("cards")}>Cards</button>
              <button type="button" className="px-3 py-2 text-xs font-semibold" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
            </div>
          </div>

          {cases.loading ? <LoadingBlock label="Loading cases" /> : cases.error ? <ErrorState message={cases.error} onRetry={cases.reload} /> : !cases.data?.items.length ? (
            <EmptyState title={q || status ? "No matching cases" : "No investigations yet"} action={<Link className="btn btn-primary" to="/app/cases/new">Create the first case</Link>}>
              Create a case, attach evidence and run the multi-agent analysis.
            </EmptyState>
          ) : (
            <>
              {view === "cards" ? (
                <ul className="grid sm:grid-cols-2 2xl:grid-cols-3 gap-3">
                  {cases.data.items.map((c, i) => (
                    <GlowCard as="li" key={c.id} className="case-card p-4 rise-in"
                      style={{ animationDelay: `${i * 40}ms`, ["--c" as string]: CONCERN_EDGE[c.overall_concern ?? ""] ?? "var(--color-line)" }}>
                      <div className="flex items-center gap-2 text-xs"><span className="mono text-muted">{c.reference}</span><span className="ml-auto"><StatusBadge status={c.status} /></span></div>
                      <Link className="block font-semibold mt-2 hover:text-cyan truncate after:absolute after:inset-0" to={`/app/cases/${c.id}`}>{c.title}</Link>
                      <div className="flex flex-wrap items-center gap-2 mt-3 text-xs">
                        <ConcernBadge level={c.overall_concern} />
                        <span className="chip text-muted">{c.evidence_count} file{c.evidence_count === 1 ? "" : "s"}</span>
                        {c.latest_job_status && <StatusBadge status={c.latest_job_status} />}
                      </div>
                      <p className="text-muted text-xs mt-3">Updated {fmtDate(c.updated_at)}</p>
                    </GlowCard>
                  ))}
                </ul>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">Investigation cases</caption>
                    <thead><tr className="text-left text-muted border-b border-line"><th className="py-2 pr-3">Reference</th><th className="pr-3">Title</th><th className="pr-3">Status</th><th className="pr-3">Evidence</th><th className="pr-3">Latest analysis</th><th className="pr-3">Triage</th><th>Updated</th></tr></thead>
                    <tbody>
                      {cases.data.items.map((c) => (
                        <tr key={c.id} className="border-b border-line/50 hover:bg-panel2/40">
                          <td className="py-2 pr-3 mono text-xs">{c.reference}</td>
                          <td className="pr-3"><Link className="text-cyan hover:underline font-medium" to={`/app/cases/${c.id}`}>{c.title}</Link></td>
                          <td className="pr-3"><StatusBadge status={c.status} /></td>
                          <td className="pr-3">{c.evidence_count}</td>
                          <td className="pr-3"><StatusBadge status={c.latest_job_status} /></td>
                          <td className="pr-3"><ConcernBadge level={c.overall_concern} /></td>
                          <td className="text-muted text-xs">{fmtDate(c.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <nav aria-label="Case pages" className="flex items-center justify-end gap-2 mt-4 text-sm">
                <button className="btn btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
                <span aria-live="polite">Page {page} of {pages}</span>
                <button className="btn btn-ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
              </nav>
            </>
          )}
        </section>

        <section className="card p-4" aria-labelledby="act-h">
          <h2 id="act-h" className="font-semibold mb-4">Recent activity</h2>
          {d?.recent_activity.length ? <ActivityTimeline items={d.recent_activity} /> : <p className="text-muted text-sm">Nothing yet – activity appears here as you work.</p>}
        </section>
      </div>
    </div>
  );
}
