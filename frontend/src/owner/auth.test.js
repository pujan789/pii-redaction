import { webcrypto } from "node:crypto";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { finishSignIn, signIn } from "./auth";

const config = {
  client_id: "owner-client", login_domain: "https://synthetic.auth.us-east-1.amazoncognito.com",
  callback_url: "https://taxhance.com/pii-redaction/owner-console/",
};
const assign = vi.fn();
beforeEach(() => {
  sessionStorage.clear(); assign.mockClear();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("location", { origin: "https://taxhance.com", pathname: "/pii-redaction/owner-console/", search: "", assign });
  vi.stubGlobal("history", { replaceState: vi.fn() });
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

describe("owner sign-in", () => {
  it("uses authorization code and a PKCE challenge without a client secret", async () => {
    await signIn(config);
    const destination = new URL(assign.mock.calls[0][0]);
    expect(destination.searchParams.get("response_type")).toBe("code");
    expect(destination.searchParams.get("code_challenge_method")).toBe("S256");
    expect(destination.searchParams.get("code_challenge")).toHaveLength(43);
    expect(destination.searchParams.get("scope")).toContain("pii-analytics/read");
    const transaction = JSON.parse(sessionStorage.getItem("pii-owner-sign-in"));
    expect(destination.searchParams.get("state")).toBe(transaction.state);
    expect(destination.href).not.toContain(transaction.verifier);
  });
  it("rejects a mismatched state before exchanging a code", async () => {
    await signIn(config);
    location.search = "?code=synthetic&state=wrong";
    await expect(finishSignIn(config)).rejects.toThrow("expired");
    expect(fetch).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("pii-owner-sign-in")).toBeNull();
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/pii-redaction/owner-console/");
  });
  it("keeps the access token only in memory and consumes the verifier once", async () => {
    await signIn(config);
    const transaction = JSON.parse(sessionStorage.getItem("pii-owner-sign-in"));
    location.search = `?code=synthetic&state=${transaction.state}`;
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ access_token: "synthetic-access", token_type: "Bearer", expires_in: 1800, refresh_token: "discard-me" })));
    const session = await finishSignIn(config);
    expect(session?.accessToken).toBe("synthetic-access");
    expect(sessionStorage.length).toBe(0);
    const body = vi.mocked(fetch).mock.calls[0][1].body;
    expect(body.get("code_verifier")).toBe(transaction.verifier);
    await expect(finishSignIn(config)).rejects.toThrow("expired");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("does not redirect sign-in to an unrelated callback", async () => {
    await expect(signIn({ ...config, callback_url: "https://attacker.example/" })).rejects.toThrow("configured owner page");
    expect(assign).not.toHaveBeenCalled();
  });
});
