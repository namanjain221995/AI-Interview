import { expect, type APIRequestContext } from "@playwright/test";

export interface StaffSession {
  request: APIRequestContext;
  csrf: string;
}

function csrfFrom(cookies: Array<{ name: string; value: string }>): string {
  // E2E_COOKIE_PREFIX = the target stack's COOKIE_NAME_PREFIX (dev_ for the dev copy on :3201).
  return cookies.find((c) => c.name === `${process.env.E2E_COOKIE_PREFIX ?? ""}csrf_token`)?.value ?? "";
}

/** Log a staff user in through the real API and return a request context carrying the cookies + CSRF token. */
export async function staffLogin(request: APIRequestContext, email: string, password: string): Promise<StaffSession> {
  const res = await request.post("/api/v1/auth/login", { data: { email, password } });
  expect(res.status(), await res.text()).toBe(200);
  const state = await request.storageState();
  return { request, csrf: csrfFrom(state.cookies) };
}

export async function apiPost<T>(s: StaffSession, url: string, data: unknown): Promise<T> {
  const res = await s.request.post(url, { data, headers: { "X-CSRF-Token": s.csrf } });
  expect(res.ok(), `${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function apiPatch<T>(s: StaffSession, url: string, data: unknown): Promise<T> {
  const res = await s.request.patch(url, { data, headers: { "X-CSRF-Token": s.csrf } });
  expect(res.ok(), `${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function apiGet<T>(s: StaffSession, url: string): Promise<T> {
  const res = await s.request.get(url);
  expect(res.ok(), `${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function poll<T>(fn: () => Promise<T | null | undefined>, opts: { timeoutMs: number; intervalMs?: number; what: string }): Promise<T> {
  const deadline = Date.now() + opts.timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (err) {
      last = err;
    }
    await new Promise((r) => setTimeout(r, opts.intervalMs ?? 2000));
  }
  throw new Error(`timed out waiting for ${opts.what}${last ? `: ${String(last)}` : ""}`);
}

/** POST to a test-only control endpoint (/api/v1/test/*) with the dev shared secret. */
export async function testHookPost<T>(s: StaffSession, url: string, data: unknown, token: string): Promise<T> {
  const res = await s.request.post(url, { data, headers: { "X-CSRF-Token": s.csrf, "X-Test-Hooks-Token": token } });
  expect(res.ok(), `${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}
