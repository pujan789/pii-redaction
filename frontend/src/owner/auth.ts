export interface OwnerConfig {
  client_id: string;
  login_domain: string;
  callback_url: string;
}

export interface Session {
  accessToken: string;
  expiresAt: number;
}

const TRANSACTION = "pii-owner-sign-in";
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function loginBase(config: OwnerConfig): string {
  const url = new URL(config.login_domain);
  if (url.protocol !== "https:" || !url.hostname.endsWith(".amazoncognito.com") || url.username || url.password) {
    throw new Error("Sign-in configuration is unavailable.");
  }
  return url.origin;
}

export async function loadConfig(): Promise<OwnerConfig> {
  const response = await fetch(`${API_BASE}/v1/owner/config`, { credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new Error("Analytics sign-in has not been configured yet.");
  const config = await response.json() as OwnerConfig;
  loginBase(config);
  if (!config.client_id || !config.callback_url) throw new Error("Sign-in configuration is unavailable.");
  return config;
}

export async function signIn(config: OwnerConfig): Promise<void> {
  const callback = new URL(config.callback_url);
  if (callback.origin !== location.origin || callback.pathname !== location.pathname) {
    throw new Error("Open the configured owner page to sign in.");
  }
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  sessionStorage.setItem(TRANSACTION, JSON.stringify({ verifier, state, createdAt: Date.now() }));
  const url = new URL(`${loginBase(config)}/oauth2/authorize`);
  url.search = new URLSearchParams({
    response_type: "code", client_id: config.client_id, redirect_uri: config.callback_url,
    scope: "openid email pii-analytics/read", state,
    code_challenge_method: "S256", code_challenge: challenge,
  }).toString();
  location.assign(url.href);
}

export async function finishSignIn(config: OwnerConfig): Promise<Session | null> {
  const params = new URLSearchParams(location.search);
  if (!params.has("code") && !params.has("error")) return null;
  // Remove the one-time authorization code from browser history immediately.
  history.replaceState(null, "", location.pathname);
  const saved = sessionStorage.getItem(TRANSACTION);
  sessionStorage.removeItem(TRANSACTION);
  if (params.has("error")) throw new Error("Sign-in was cancelled or denied. Please try again.");
  const transaction = saved ? JSON.parse(saved) as { verifier: string; state: string; createdAt: number } : null;
  if (!transaction || !transaction.verifier || transaction.state !== params.get("state") ||
      !Number.isFinite(transaction.createdAt) || Date.now() - transaction.createdAt > 600_000 ||
      transaction.createdAt > Date.now()) {
    throw new Error("This sign-in request has expired. Please sign in again.");
  }
  const response = await fetch(`${loginBase(config)}/oauth2/token`, {
    method: "POST", credentials: "omit", referrerPolicy: "no-referrer", redirect: "error",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code", client_id: config.client_id,
      redirect_uri: config.callback_url, code: params.get("code") ?? "",
      code_verifier: transaction.verifier,
    }),
  });
  if (!response.ok) throw new Error("Sign-in could not be completed. Please try again.");
  const result = await response.json() as { access_token?: string; expires_in?: number; token_type?: string };
  if (!result.access_token || result.token_type?.toLowerCase() !== "bearer" ||
      !Number.isFinite(result.expires_in) || (result.expires_in ?? 0) <= 0) {
    throw new Error("Sign-in returned an invalid session.");
  }
  // Access tokens live only in memory. Refresh and ID tokens are discarded.
  return { accessToken: result.access_token, expiresAt: Date.now() + Math.min(result.expires_in!, 3600) * 1000 };
}

export function signOut(config: OwnerConfig): void {
  sessionStorage.removeItem(TRANSACTION);
  const url = new URL(`${loginBase(config)}/logout`);
  url.search = new URLSearchParams({ client_id: config.client_id, logout_uri: config.callback_url }).toString();
  location.assign(url.href);
}
