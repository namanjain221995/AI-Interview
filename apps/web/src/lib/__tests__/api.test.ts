import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, buildUrl, errorFromResponse, getCsrfToken, onUnauthorized, refreshSession } from "@/lib/api";

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function errorEnvelope(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
  return json(status, { error: { code, message, request_id: "req-123", details: {}, ...extra } });
}

function setCookie(value: string) {
  document.cookie = value;
}

function clearCookies() {
  for (const part of document.cookie.split(";")) {
    const name = part.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
}

function headersOf(call: FetchArgs): Headers {
  return new Headers(call[1]?.headers as HeadersInit);
}

function urlOf(call: FetchArgs): string {
  return String(call[0]);
}

let fetchMock: ReturnType<typeof vi.fn<(...args: FetchArgs) => Promise<Response>>>;

beforeEach(() => {
  fetchMock = vi.fn<(...args: FetchArgs) => Promise<Response>>();
  vi.stubGlobal("fetch", fetchMock);
  clearCookies();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearCookies();
});

describe("buildUrl", () => {
  it("prefixes /api/v1 and serialises params, skipping empty values", () => {
    expect(buildUrl("/jobs", { limit: 20, offset: 0, status: undefined, q: "", tags: ["a", "b"], active: true })).toBe(
      "/api/v1/jobs?limit=20&offset=0&tags=a&tags=b&active=true",
    );
  });

  it("keeps explicit /api paths and rejects absolute URLs", () => {
    expect(buildUrl("/api/v1/media/signed/abc")).toBe("/api/v1/media/signed/abc");
    expect(() => buildUrl("https://evil.example/jobs")).toThrow(/relative/);
    expect(() => buildUrl("//evil.example/jobs")).toThrow(/relative/);
  });
});

describe("CSRF header", () => {
  it("reads the csrf_token cookie", () => {
    setCookie("csrf_token=abc%3D123; path=/");
    expect(getCsrfToken()).toBe("abc=123");
  });

  it("is sent on unsafe methods and never on GET", async () => {
    setCookie("csrf_token=tok-1; path=/");
    fetchMock.mockImplementation(async () => json(200, { ok: true }));

    await api.get("/jobs");
    await api.post("/jobs", { title: "Engineer" });
    await api.patch("/jobs/1", { title: "Senior engineer" });
    await api.put("/settings", {});
    await api.delete("/requirements/1");

    const [getCall, postCall, patchCall, putCall, deleteCall] = fetchMock.mock.calls;
    expect(headersOf(getCall!).has("X-CSRF-Token")).toBe(false);
    for (const call of [postCall!, patchCall!, putCall!, deleteCall!]) {
      expect(headersOf(call).get("X-CSRF-Token")).toBe("tok-1");
    }
    expect(postCall![1]?.credentials).toBe("same-origin");
    expect(headersOf(postCall!).get("Content-Type")).toBe("application/json");
    expect(postCall![1]?.body).toBe(JSON.stringify({ title: "Engineer" }));
  });

  it("uses the rotated cookie on the retry after a refresh", async () => {
    setCookie("csrf_token=old; path=/");
    fetchMock
      .mockResolvedValueOnce(errorEnvelope(401, "unauthorized", "Expired"))
      .mockImplementationOnce(async () => {
        setCookie("csrf_token=new; path=/");
        return json(200, {});
      })
      .mockResolvedValueOnce(json(201, { id: "j1" }));

    await expect(api.post("/jobs", { title: "x" })).resolves.toEqual({ id: "j1" });
    const calls = fetchMock.mock.calls;
    expect(urlOf(calls[1]!)).toBe("/api/v1/auth/refresh");
    expect(headersOf(calls[1]!).get("X-CSRF-Token")).toBe("old");
    expect(headersOf(calls[2]!).get("X-CSRF-Token")).toBe("new");
  });
});

describe("401 refresh-and-retry", () => {
  it("refreshes once and retries the original request", async () => {
    fetchMock
      .mockResolvedValueOnce(errorEnvelope(401, "unauthorized", "Expired"))
      .mockResolvedValueOnce(json(200, {}))
      .mockResolvedValueOnce(json(200, { items: [], total: 0 }));

    await expect(api.get("/jobs")).resolves.toEqual({ items: [], total: 0 });
    expect(fetchMock.mock.calls.map((c) => [c[1]?.method, urlOf(c)])).toEqual([
      ["GET", "/api/v1/jobs"],
      ["POST", "/api/v1/auth/refresh"],
      ["GET", "/api/v1/jobs"],
    ]);
  });

  it("shares one refresh between concurrent 401s", async () => {
    let releaseRefresh: (r: Response) => void = () => {};
    const refreshGate = new Promise<Response>((resolve) => {
      releaseRefresh = resolve;
    });
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh")) return refreshGate;
      const attempt = fetchMock.mock.calls.filter((c) => String(c[0]) === url && c[1]?.method === init?.method).length;
      return attempt === 1 ? errorEnvelope(401, "unauthorized", "Expired") : json(200, { url });
    });

    const pending = Promise.all([api.get("/a"), api.get("/b"), api.get("/c")]);
    await vi.waitFor(() => expect(fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/auth/refresh"))).toHaveLength(1));
    releaseRefresh(json(200, {}));
    const results = await pending;

    expect(results).toEqual([{ url: "/api/v1/a" }, { url: "/api/v1/b" }, { url: "/api/v1/c" }]);
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/auth/refresh"))).toHaveLength(1);
  });

  it("fails with the original 401 and notifies listeners once when the refresh is rejected", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    fetchMock.mockImplementation(async (input) =>
      String(input).endsWith("/auth/refresh") ? errorEnvelope(401, "refresh_invalid", "No session") : errorEnvelope(401, "unauthorized", "Expired"),
    );

    const results = await Promise.allSettled([api.get("/a"), api.get("/b")]);
    unsubscribe();

    for (const result of results) {
      expect(result.status).toBe("rejected");
      const error = (result as PromiseRejectedResult).reason as ApiError;
      expect(error).toBeInstanceOf(ApiError);
      expect(error.status).toBe(401);
      expect(error.code).toBe("unauthorized");
    }
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("does not refresh for credential checks (login)", async () => {
    fetchMock.mockResolvedValueOnce(errorEnvelope(401, "invalid_credentials", "Bad credentials"));
    await expect(api.post("/auth/login", { email: "a@b.co", password: "x" })).rejects.toMatchObject({ code: "invalid_credentials" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("honours skipAuthRefresh", async () => {
    fetchMock.mockResolvedValueOnce(errorEnvelope(401, "unauthorized", "No"));
    await expect(api.get("/auth/me", { skipAuthRefresh: true })).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not sign the user out when the refresh endpoint is unreachable", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    fetchMock.mockResolvedValueOnce(errorEnvelope(401, "unauthorized", "Expired")).mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(api.get("/jobs")).rejects.toMatchObject({ code: "session_refresh_unavailable", status: 503 });
    unsubscribe();
    expect(listener).not.toHaveBeenCalled();
  });

  it("refreshSession reports the outcome", async () => {
    fetchMock.mockResolvedValueOnce(json(200, {}));
    await expect(refreshSession()).resolves.toBe("refreshed");
    fetchMock.mockResolvedValueOnce(json(500, {}));
    await expect(refreshSession()).resolves.toBe("unavailable");
  });
});

describe("errors", () => {
  it("maps the backend envelope onto ApiError", async () => {
    fetchMock.mockResolvedValueOnce(
      errorEnvelope(422, "validation_failed", "Some fields need attention.", {
        details: { errors: [{ loc: ["body", "email"], msg: "Invalid email" }], fields: { title: "Required" } },
      }),
    );
    const error = (await api.post("/candidates", {}).catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.code).toBe("validation_failed");
    expect(error.message).toBe("Some fields need attention.");
    expect(error.requestId).toBe("req-123");
    expect(error.isValidation).toBe(true);
    expect(error.fieldErrors()).toEqual({ title: "Required", email: "Invalid email" });
  });

  it("falls back to status defaults, the request-id header and Retry-After", () => {
    const error = errorFromResponse({ status: 429, header: (n) => ({ "x-request-id": "hdr-9", "retry-after": "42" })[n.toLowerCase()] ?? null, text: "" }, undefined);
    expect(error.code).toBe("rate_limited");
    expect(error.requestId).toBe("hdr-9");
    expect(error.retryAfterSeconds).toBe(42);
    expect(error.isRateLimited).toBe(true);
  });

  it("understands FastAPI's default {detail} shape", async () => {
    fetchMock.mockResolvedValueOnce(json(404, { detail: "Not Found" }));
    await expect(api.get("/missing")).rejects.toMatchObject({ status: 404, code: "not_found", message: "Not Found" });
  });

  it("turns network failures into a network_error ApiError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.get("/jobs")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("rethrows aborts untouched so query cancellation works", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const pending = api.get("/slow", { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("times out slow requests", async () => {
    fetchMock.mockImplementationOnce(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    await expect(api.get("/slow", { timeoutMs: 20 })).rejects.toMatchObject({ code: "timeout", status: 0 });
  });

  it("returns undefined for 204 responses", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.delete("/requirements/1")).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------- uploads

interface FakeXhr {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
  upload: { onprogress: ((event: ProgressEvent) => void) | null };
  respond(status: number, body: unknown, headers?: Record<string, string>): void;
}

function installFakeXhr(onSend: (xhr: FakeXhr) => void) {
  class FakeXMLHttpRequest implements FakeXhr {
    method = "";
    url = "";
    headers: Record<string, string> = {};
    body: unknown = null;
    status = 0;
    responseText = "";
    withCredentials = false;
    upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    private responseHeaders: Record<string, string> = {};
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    getResponseHeader(name: string) {
      return this.responseHeaders[name.toLowerCase()] ?? null;
    }
    abort() {
      this.onabort?.();
    }
    send(body: unknown) {
      this.body = body;
      onSend(this);
    }
    respond(status: number, body: unknown, headers: Record<string, string> = { "content-type": "application/json" }) {
      this.status = status;
      this.responseText = body === undefined ? "" : JSON.stringify(body);
      this.responseHeaders = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
      this.onload?.();
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
}

describe("upload", () => {
  it("posts multipart with CSRF and reports progress", async () => {
    setCookie("csrf_token=up-1; path=/");
    const sent: FakeXhr[] = [];
    installFakeXhr((xhr) => {
      sent.push(xhr);
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 200 } as ProgressEvent);
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 200, total: 200 } as ProgressEvent);
      xhr.respond(201, { id: "r1", status: "uploaded" });
    });
    const progress = vi.fn();
    const file = new File(["%PDF-1.7"], "resume.pdf", { type: "application/pdf" });

    await expect(api.upload("/candidates/c1/resumes", file, { onProgress: progress, fields: { job_id: "j1" } })).resolves.toEqual({
      id: "r1",
      status: "uploaded",
    });
    expect(sent[0]!.method).toBe("POST");
    expect(sent[0]!.url).toBe("/api/v1/candidates/c1/resumes");
    expect(sent[0]!.headers["X-CSRF-Token"]).toBe("up-1");
    const form = sent[0]!.body as FormData;
    expect((form.get("file") as File).name).toBe("resume.pdf");
    expect(form.get("job_id")).toBe("j1");
    expect(progress).toHaveBeenNthCalledWith(1, { loaded: 50, total: 200, percent: 25 });
    expect(progress).toHaveBeenLastCalledWith({ loaded: 200, total: 200, percent: 100 });
  });

  it("refreshes on 401 and re-sends the same form", async () => {
    let attempts = 0;
    installFakeXhr((xhr) => {
      attempts += 1;
      if (attempts === 1) xhr.respond(401, { error: { code: "unauthorized", message: "Expired" } });
      else xhr.respond(202, { accepted: true });
    });
    fetchMock.mockResolvedValueOnce(json(200, {}));
    await expect(api.upload("/portal/sessions/s1/frames", new Blob(["x"], { type: "image/jpeg" }))).resolves.toEqual({ accepted: true });
    expect(attempts).toBe(2);
    expect(urlOf(fetchMock.mock.calls[0]!)).toBe("/api/v1/auth/refresh");
  });

  it("surfaces server rejections as ApiError", async () => {
    installFakeXhr((xhr) => xhr.respond(413, { error: { code: "payload_too_large", message: "The file is larger than 10 MB." } }));
    await expect(api.upload("/portal/interviews/i1/resume", new File(["x"], "big.pdf"))).rejects.toMatchObject({
      status: 413,
      code: "payload_too_large",
      message: "The file is larger than 10 MB.",
    });
  });
});
