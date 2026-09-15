type Page = "landing" | "app" | "self_hosting";

// Fixed page names only: no URLs, query strings, referrers, filenames or job data.
function send(path: string, payload: object): void {
  const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
  try {
    void Promise.resolve(fetch(`${apiBase}/v1/analytics/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      keepalive: true,
      body: JSON.stringify(payload),
    })).catch(() => { /* Analytics must not affect the document workflow. */ });
  } catch { /* Browser restrictions must not affect the document workflow. */ }
}

export function recordPageView(page: Page): void {
  let newVisit = false;
  try {
    const last = Number(sessionStorage.getItem("pii-usage-last-view"));
    newVisit = !last || Date.now() - last > 30 * 60 * 1000;
    sessionStorage.setItem("pii-usage-last-view", String(Date.now()));
  } catch { /* Page views still work when browser storage is disabled. */ }
  send("page-view", { page, new_visit: newVisit });
}

export function recordUsageAction(action: "download" | "batch", documents: number): void {
  if (Number.isInteger(documents) && documents > 0 && documents <= 65_535) {
    send("action", { action, documents });
  }
}

const page = document.documentElement.dataset.analyticsPage;
if (page === "landing" || page === "self_hosting") recordPageView(page);
