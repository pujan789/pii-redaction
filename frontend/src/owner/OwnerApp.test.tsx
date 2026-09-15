import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import OwnerApp, { Report, type Day } from "./OwnerApp";
import { finishSignIn, loadConfig } from "./auth";

vi.mock("./auth", () => ({ API_BASE: "", loadConfig: vi.fn(), finishSignIn: vi.fn(), signIn: vi.fn(), signOut: vi.fn() }));
const emptyDay: Day = { date: "2026-09-15", visits: 0, landing_views: 0, app_views: 0, self_hosting_views: 0, documents_submitted: 0, documents_completed: 0, pages_completed: 0, documents_failed: 0, documents_downloaded: 0, downloads_started: 0, batches_selected: 0, batch_documents_selected: 0, processing_seconds: 0, processing_tasks: 0 };
beforeEach(() => {
  vi.mocked(loadConfig).mockResolvedValue({ client_id: "test", callback_url: "https://example.com/owner-console/", login_domain: "https://synthetic.amazoncognito.com" });
  vi.mocked(finishSignIn).mockResolvedValue(null);
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

describe("private analytics page", () => {
  it("does not request analytics before sign-in", async () => {
    render(<OwnerApp />);
    expect(await screen.findByRole("button", { name: /sign in securely/i })).toBeEnabled();
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByText("Activity overview")).not.toBeInTheDocument();
  });
  it("requests the selected period using the access token", async () => {
    vi.mocked(finishSignIn).mockResolvedValue({ accessToken: "synthetic-access", expiresAt: Date.now() + 60_000 });
    vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({ days: [emptyDay], generated_at: "2026-09-15T12:00:00Z", timezone: "UTC" })));
    render(<OwnerApp />);
    await screen.findByText("No activity recorded yet");
    expect(fetch).toHaveBeenCalledWith("/v1/owner/analytics?days=30", expect.objectContaining({ headers: { Authorization: "Bearer synthetic-access" }, cache: "no-store" }));
    fireEvent.change(screen.getByLabelText("Reporting period"), { target: { value: "7" } });
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith("/v1/owner/analytics?days=7", expect.anything()));
  });
  it("returns to sign-in when the API refuses the account", async () => {
    vi.mocked(finishSignIn).mockResolvedValue({ accessToken: "synthetic-access", expiresAt: Date.now() + 60_000 });
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 403 }));
    render(<OwnerApp />);
    expect(await screen.findByRole("alert")).toHaveTextContent("does not have owner access");
    expect(screen.queryByText("Daily breakdown")).not.toBeInTheDocument();
  });
  it("calculates task time using weighted totals across days", () => {
    render(<Report data={{ timezone: "UTC", generated_at: "2026-09-15T12:00:00Z", days: [
      { ...emptyDay, date: "2026-09-14", processing_seconds: 10, processing_tasks: 1 },
      { ...emptyDay, processing_seconds: 90, processing_tasks: 3 },
    ] }} />);
    const panel = screen.getByRole("heading", { name: "Processing" }).closest("section")!;
    expect(within(panel).getByText("25.0s")).toBeInTheDocument();
  });
});
