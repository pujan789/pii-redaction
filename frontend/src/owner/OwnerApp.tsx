import { useEffect, useState } from "react";
import { API_BASE, finishSignIn, loadConfig, signIn, signOut, type OwnerConfig, type Session } from "./auth";

export interface Day {
  date: string;
  visits: number;
  landing_views: number;
  app_views: number;
  self_hosting_views: number;
  documents_submitted: number;
  documents_completed: number;
  pages_completed: number;
  documents_failed: number;
  documents_downloaded: number;
  downloads_started: number;
  batches_selected: number;
  batch_documents_selected: number;
  processing_seconds: number;
  processing_tasks: number;
}
export interface Dashboard { days: Day[]; generated_at: string; timezone: "UTC" }
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dateLabel = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const views = (day: Day) => day.landing_views + day.app_views + day.self_hosting_views;

function Trend({ days }: { days: Day[] }) {
  const maximum = Math.max(1, ...days.flatMap((day) => [views(day), day.documents_completed]));
  const points = (value: (day: Day) => number) => days.map((day, i) => `${48 + (i / Math.max(1, days.length - 1)) * 880},${218 - (value(day) / maximum) * 185}`).join(" ");
  const active = days.some((day) => views(day) || day.documents_completed);
  return <div className="trend">
    <div className="section-heading"><h2>Daily activity</h2><div className="legend"><span><i className="dot views-dot" />Page views</span><span><i className="dot documents-dot" />Completed documents</span></div></div>
    {active ? <svg viewBox="0 0 960 260" role="img" aria-label="Daily page views and completed documents. Exact values are in the daily breakdown below.">
      {[0, 0.5, 1].map((fraction) => <g key={fraction}><line x1="48" x2="928" y1={218 - fraction * 185} y2={218 - fraction * 185} className="grid-line" /><text x="35" y={223 - fraction * 185} textAnchor="end">{number.format(maximum * fraction)}</text></g>)}
      <polyline points={points(views)} className="views-line" />
      <polyline points={points((day) => day.documents_completed)} className="documents-line" />
      {days.length > 0 && <><text x="48" y="252">{dateLabel(days[0].date)}</text><text x="928" y="252" textAnchor="end">{dateLabel(days[days.length - 1].date)}</text></>}
    </svg> : <div className="chart-empty"><span className="empty-mark" aria-hidden="true">↗</span><h3>No activity recorded yet</h3><p>New visits and document activity will appear here after collection starts.</p></div>}
  </div>;
}

export function Report({ data }: { data: Dashboard }) {
  const sum = (field: keyof Omit<Day, "date">) => data.days.reduce((total, day) => total + day[field], 0);
  const pageViews = data.days.reduce((total, day) => total + views(day), 0);
  const tasks = sum("processing_tasks");
  const average = tasks ? sum("processing_seconds") / tasks : null;
  return <>
    <div className="stats">
      {[
        ["Visits", sum("visits"), "Estimated browsing sessions"],
        ["Documents submitted", sum("documents_submitted"), "Uploads queued for processing"],
        ["Documents downloaded", sum("documents_downloaded"), "PDF and ZIP saves started"],
        ["Pages redacted", sum("pages_completed"), "In completed documents"],
      ].map(([label, value, note]) => <section className="stat" key={label}><h2>{label}</h2><strong>{number.format(Number(value))}</strong><p>{note}</p></section>)}
    </div>
    <Trend days={data.days} />
    <div className="report-details">
      <section className="traffic-panel"><h2>Pages visited</h2><ul>{[
        ["PII Redaction", sum("landing_views")], ["Redaction app", sum("app_views")], ["Self-hosting", sum("self_hosting_views")],
      ].map(([label, count]) => <li key={label}><span>{label}</span><strong>{number.format(Number(count))}</strong><div className="traffic-track"><span style={{ width: `${pageViews ? Number(count) / pageViews * 100 : 0}%` }} /></div></li>)}</ul><p className="muted">Page loads, including repeat visits. These are not unique visitor counts.</p></section>
      <section className="processing-panel"><h2>Processing</h2><div className="processing-row"><span>Completed documents</span><strong>{number.format(sum("documents_completed"))}</strong></div><div className="processing-row"><span>Failed documents</span><strong className={sum("documents_failed") ? "failure-count" : ""}>{number.format(sum("documents_failed"))}</strong></div><div className="processing-row"><span>Average task time</span><strong>{average === null ? "—" : `${average.toFixed(1)}s`}</strong></div><div className="processing-row"><span>Batches selected</span><strong>{number.format(sum("batches_selected"))}</strong></div><div className="processing-row"><span>Average batch size</span><strong>{sum("batches_selected") ? (sum("batch_documents_selected") / sum("batches_selected")).toFixed(1) : "—"}</strong></div><p className="muted">Task time excludes queue time and human review. Manual jobs can have separate detection and rendering tasks.</p></section>
    </div>
    <section className="daily-panel"><div className="section-heading"><h2>Daily breakdown</h2><span className="muted">UTC</span></div><div className="table-scroll"><table><thead><tr><th>Date</th><th>Visits</th><th>Page views</th><th>Uploaded</th><th>Completed</th><th>Downloaded</th><th>Pages</th><th>Failed</th></tr></thead><tbody>{[...data.days].reverse().map((day) => <tr key={day.date}><th scope="row">{dateLabel(day.date)}</th><td>{number.format(day.visits)}</td><td>{number.format(views(day))}</td><td>{number.format(day.documents_submitted)}</td><td>{number.format(day.documents_completed)}</td><td>{number.format(day.documents_downloaded)}</td><td>{number.format(day.pages_completed)}</td><td>{number.format(day.documents_failed)}</td></tr>)}</tbody></table></div></section>
    <p className="muted metric-notes">Visits are estimated per browser tab after 30 minutes without a page load. Downloads count PDFs when the browser starts saving; repeat downloads count again. Neither metric identifies individual people or confirms that a file was saved to disk.</p>
  </>;
}

export default function OwnerApp() {
  const [config, setConfig] = useState<OwnerConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(30);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Dashboard | null>(null);

  useEffect(() => {
    let active = true;
    void loadConfig().then(async (loaded) => {
      if (active) setConfig(loaded);
      const result = await finishSignIn(loaded);
      if (active) setSession(result);
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Sign-in is unavailable."); })
      .finally(() => { if (active) setInitializing(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => {
      setSession(null); setData(null); setError("Your session has expired. Sign in to continue.");
    }, Math.max(0, session.expiresAt - Date.now()));
    return () => clearTimeout(timer);
  }, [session]);

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    setLoading(true); setError(""); setData(null);
    void fetch(`${API_BASE}/v1/owner/analytics?days=${days}`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
      credentials: "omit", cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (response.status === 401 || response.status === 403) {
        setSession(null);
        throw new Error(response.status === 403 ? "This account does not have owner access." : "Your session has expired. Sign in to continue.");
      }
      if (response.ok && response.headers.get("content-type")?.includes("text/html")) {
        setSession(null);
        throw new Error("Owner access could not be verified. Please sign in with your owner account.");
      }
      if (!response.ok) throw new Error("Analytics could not be loaded. Please try again.");
      const result = await response.json() as Dashboard;
      if (!Array.isArray(result.days)) throw new Error("Analytics could not be loaded. Please try again.");
      if (!controller.signal.aborted) setData(result);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Analytics could not be loaded.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [session, days, refresh]);

  async function startSignIn() {
    if (!config) return;
    setError("");
    try { await signIn(config); } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in could not be started."); }
  }

  return <div className="owner-shell">
    <header className="owner-header"><div className="owner-brand"><img src="../taxhance-logo.png" alt="Taxhance" /><span>PII Redaction</span><span className="owner-badge">Owner</span></div>{session && <button className="text-button" onClick={() => { setSession(null); setData(null); if (config) signOut(config); }}>Sign out</button>}</header>
    {!session ? <main className="sign-in"><div className="sign-in-rule" /><p className="eyebrow">PRIVATE ACCESS</p><h1>Sign in to analytics</h1><p className="sign-in-description">Website activity and redaction usage, in one place.</p>{error && <div className="notice error" role="alert">{error}</div>}<button className="primary-button" disabled={initializing || !config} onClick={() => void startSignIn()}>{initializing ? "Preparing sign-in…" : "Sign in securely"}<span aria-hidden="true">↗</span></button><p className="muted">Access is limited to the owner account.</p></main> :
      <main className="owner-main"><div className="report-heading"><div><p className="eyebrow">PII REDACTION</p><h1>Activity overview</h1><p className="muted">Aggregate usage · All dates in UTC</p></div><div className="report-controls"><label className="sr-only" htmlFor="period">Reporting period</label><select id="period" value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select><button className="secondary-button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>{loading ? "Refreshing…" : "Refresh"}</button></div></div>
        {error && <div className="notice error" role="alert">{error}<button className="text-button" onClick={() => setRefresh((value) => value + 1)}>Try again</button></div>}
        {loading && <div className="report-loading" role="status">Loading your analytics…</div>}
        {data && <Report data={data} />}
        <footer className="report-footer"><span>Counts can take a few minutes to appear.</span>{data && <span>Updated {new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}</footer>
      </main>}
  </div>;
}
