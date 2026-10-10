/**
 * The only way the SPA talks to the REST API (CONTRACTS §3, §4, §10).
 *
 * - Same-origin cookies carry the session (access/refresh tokens are HttpOnly).
 * - Unsafe methods send `X-CSRF-Token` copied from the readable `csrf_token`
 *   cookie (double-submit). The cookie is re-read on every attempt because a
 *   refresh may rotate it.
 * - A 401 triggers exactly one `POST /auth/refresh` (shared by all concurrent
 *   callers) and one retry. If the refresh is rejected, unauthorized listeners
 *   fire once and the original request fails with the 401 ApiError.
 * - Errors are normalised from the backend envelope
 *   `{"error": {"code", "message", "request_id", "details"}}` into ApiError.
 */

export const API_BASE = "/api/v1";
export const CSRF_COOKIE = "csrf_token";
export const CSRF_HEADER = "X-CSRF-Token";
export const DEFAULT_TIMEOUT_MS = 30_000;

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
/** Endpoints whose 401 means "wrong credentials", never "expired session". */
const NO_REFRESH_PREFIXES = ["/auth/login", "/auth/refresh", "/auth/logout", "/candidate-access/"];

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
type Primitive = string | number | boolean;
export type QueryParams = Record<string, Primitive | null | undefined | readonly Primitive[]>;

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  requestId?: string | null;
  details?: Record<string, unknown>;
  retryAfterSeconds?: number | null;
  cause?: unknown;
}

export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;
  readonly retryAfterSeconds: number | null;

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? {};
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
  get isForbidden(): boolean {
    return this.status === 403;
  }
  get isNotFound(): boolean {
    return this.status === 404;
  }
  get isConflict(): boolean {
    return this.status === 409;
  }
  get isValidation(): boolean {
    return this.status === 422 || this.code === "validation_failed";
  }
  get isRateLimited(): boolean {
    return this.status === 429 || this.code === "rate_limited";
  }
  get isNetworkError(): boolean {
    return this.status === 0;
  }
  get isServerError(): boolean {
    return this.status >= 500;
  }

  /**
   * Best-effort field errors from the envelope details. Understands
   * `details.fields: {name: message}`, pydantic-style
   * `details.errors: [{loc: [...], msg}]` and `details.errors: [{field, message}]`.
   */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    const fields = this.details.fields;
    if (isRecord(fields)) {
      for (const [key, value] of Object.entries(fields)) {
        const message = Array.isArray(value) ? value.find((v) => typeof v === "string") : value;
        if (typeof message === "string") out[key] = message;
      }
    }
    const errors = this.details.errors;
    if (Array.isArray(errors)) {
      for (const item of errors) {
        if (!isRecord(item)) continue;
        const message = typeof item.msg === "string" ? item.msg : typeof item.message === "string" ? item.message : null;
        let field: string | null = typeof item.field === "string" ? item.field : null;
        if (!field && Array.isArray(item.loc)) {
          const parts = item.loc.filter((p) => p !== "body" && p !== "query" && p !== "path").map(String);
          field = parts.length ? parts.join(".") : null;
        }
        if (field && message && !(field in out)) out[field] = message;
      }
    }
    return out;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

/** Human-readable message for any thrown value. */
export function errorMessage(error: unknown, fallback = "Something went wrong. Try again."): string {
  if (isApiError(error)) return error.message || fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

// ----------------------------------------------------------------- helpers

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined" || !document.cookie) return null;
  for (const part of document.cookie.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) continue;
    if (part.slice(0, index).trim() === name) {
      const raw = part.slice(index + 1).trim();
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return null;
}

export function getCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}

/**
 * Builds a same-origin API URL. Paths are relative to `/api/v1` unless they
 * already start with `/api/`. Absolute URLs are rejected so credentials and the
 * CSRF token can never be sent to another origin.
 */
export function buildUrl(path: string, params?: QueryParams): string {
  if (/^[a-z][a-z\d+.-]*:/i.test(path) || path.startsWith("//")) {
    throw new Error(`api: only relative API paths are allowed (got "${path}")`);
  }
  const base = path.startsWith("/api/") ? path : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  if (!params) return base;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value as readonly Primitive[]) search.append(key, String(item));
    } else {
      search.append(key, String(value));
    }
  }
  const query = search.toString();
  if (!query) return base;
  return `${base}${base.includes("?") ? "&" : "?"}${query}`;
}

function apiPathOf(path: string): string {
  return path.startsWith(API_BASE) ? path.slice(API_BASE.length) || "/" : path;
}

function shouldRefreshFor(path: string): boolean {
  const apiPath = apiPathOf(path.split("?")[0] ?? path);
  return !NO_REFRESH_PREFIXES.some((prefix) => apiPath.startsWith(prefix));
}

const STATUS_CODES: Record<number, string> = {
  400: "bad_request",
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  405: "method_not_allowed",
  408: "timeout",
  409: "conflict",
  413: "payload_too_large",
  415: "unsupported_media_type",
  422: "validation_failed",
  423: "locked",
  429: "rate_limited",
  500: "internal_error",
  502: "bad_gateway",
  503: "service_unavailable",
  504: "gateway_timeout",
};

const STATUS_MESSAGES: Record<number, string> = {
  400: "The request couldn't be processed.",
  401: "Your session has ended. Sign in again.",
  403: "You don't have permission to do that.",
  404: "This item doesn't exist or was removed.",
  408: "The server took too long to respond. Try again.",
  409: "This changed since you loaded it. Refresh and try again.",
  413: "The file is too large.",
  415: "This file type isn't supported.",
  422: "Some fields need attention.",
  423: "This account is temporarily locked.",
  429: "Too many requests. Wait a moment and try again.",
  503: "The service is temporarily unavailable. Try again in a moment.",
};

function defaultCode(status: number): string {
  return STATUS_CODES[status] ?? `http_${status}`;
}

function defaultMessage(status: number): string {
  if (STATUS_MESSAGES[status]) return STATUS_MESSAGES[status];
  if (status >= 500) return "The server ran into a problem. Try again in a moment.";
  return `Request failed (HTTP ${status}).`;
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - Date.now()) / 1000));
  return null;
}

interface RawResponse {
  status: number;
  header(name: string): string | null;
  text: string;
}

function parseBody(raw: RawResponse): unknown {
  if (raw.status === 204 || raw.status === 205 || raw.text === "") return undefined;
  const contentType = raw.header("content-type") ?? "";
  if (contentType.includes("json")) {
    try {
      return JSON.parse(raw.text) as unknown;
    } catch (cause) {
      throw new ApiError({
        status: raw.status,
        code: "invalid_response",
        message: "The server sent a response that couldn't be read.",
        requestId: raw.header("x-request-id"),
        cause,
      });
    }
  }
  return raw.text;
}

export function errorFromResponse(raw: RawResponse, body: unknown): ApiError {
  const envelope = isRecord(body) && isRecord(body.error) ? body.error : null;
  const detail = !envelope && isRecord(body) ? body.detail : undefined;
  const details: Record<string, unknown> = envelope && isRecord(envelope.details)
    ? envelope.details
    : Array.isArray(detail)
      ? { errors: detail }
      : {};
  const message =
    (envelope && typeof envelope.message === "string" && envelope.message) ||
    (typeof detail === "string" && detail) ||
    defaultMessage(raw.status);
  const code = (envelope && typeof envelope.code === "string" && envelope.code) || defaultCode(raw.status);
  const requestId =
    (envelope && typeof envelope.request_id === "string" && envelope.request_id) || raw.header("x-request-id");
  const detailRetry = typeof details.retry_after_seconds === "number" ? details.retry_after_seconds : null;
  return new ApiError({
    status: raw.status,
    code,
    message,
    requestId,
    details,
    retryAfterSeconds: parseRetryAfter(raw.header("retry-after")) ?? detailRetry,
  });
}

function networkError(cause: unknown): ApiError {
  return new ApiError({
    status: 0,
    code: "network_error",
    message: "Can't reach the server. Check your connection and try again.",
    cause,
  });
}

function timeoutError(): ApiError {
  return new ApiError({ status: 0, code: "timeout", message: "The server took too long to respond. Try again." });
}

// ------------------------------------------------------- unauthorized events

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Subscribe to "the server rejected our session" (refresh failed). */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

function emitUnauthorized(): void {
  for (const listener of [...unauthorizedListeners]) {
    try {
      listener();
    } catch {
      // A failing listener must not break the request pipeline.
    }
  }
}

// --------------------------------------------- password change required (S13)

type PasswordChangeListener = () => void;
const passwordChangeListeners = new Set<PasswordChangeListener>();

/** Subscribe to "the server wants this account's password changed first" (403 password_change_required). */
export function onPasswordChangeRequired(listener: PasswordChangeListener): () => void {
  passwordChangeListeners.add(listener);
  return () => {
    passwordChangeListeners.delete(listener);
  };
}

function failed(raw: RawResponse, body: unknown): ApiError {
  const error = errorFromResponse(raw, body);
  if (error.status === 403 && error.code === "password_change_required") {
    for (const listener of [...passwordChangeListeners]) {
      try {
        listener();
      } catch {
        // A failing listener must not break the request pipeline.
      }
    }
  }
  return error;
}

// ----------------------------------------------------------------- refresh

type RefreshOutcome = "refreshed" | "denied" | "unavailable";
let refreshInFlight: Promise<RefreshOutcome> | null = null;

function csrfHeaders(method: string): Record<string, string> {
  if (!UNSAFE_METHODS.has(method)) return {};
  const token = getCsrfToken();
  return token ? { [CSRF_HEADER]: token } : {};
}

async function performRefresh(): Promise<RefreshOutcome> {
  let response: Response;
  try {
    response = await fetch(buildUrl("/auth/refresh"), {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", ...csrfHeaders("POST") },
    });
  } catch {
    return "unavailable";
  }
  if (response.ok) return "refreshed";
  if (response.status === 401 || response.status === 403) {
    emitUnauthorized();
    return "denied";
  }
  return "unavailable";
}

/** Renews the session once; concurrent callers share the same attempt. */
export function refreshSession(): Promise<RefreshOutcome> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

function refreshUnavailableError(original: ApiError): ApiError {
  return new ApiError({
    status: 503,
    code: "session_refresh_unavailable",
    message: "Couldn't renew your session. Try again in a moment.",
    requestId: original.requestId,
    cause: original,
  });
}

// ----------------------------------------------------------------- request

export interface RequestOptions extends Omit<RequestInit, "body" | "method" | "signal" | "headers"> {
  params?: QueryParams;
  /** JSON-serialised unless it is FormData, Blob, ArrayBuffer, URLSearchParams or a string. */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Default 30 s; 0 disables the timeout. */
  timeoutMs?: number;
  /** Do not attempt a session refresh on 401 (credential checks). */
  skipAuthRefresh?: boolean;
}

function isRawBody(body: unknown): body is BodyInit {
  return (
    typeof body === "string" ||
    body instanceof FormData ||
    body instanceof Blob ||
    body instanceof ArrayBuffer ||
    body instanceof URLSearchParams ||
    ArrayBuffer.isView(body)
  );
}

function linkSignal(outer: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const onAbort = () => controller.abort(outer?.reason);
  if (outer) {
    if (outer.aborted) controller.abort(outer.reason);
    else outer.addEventListener("abort", onAbort, { once: true });
  }
  if (timeoutMs > 0) {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort(new DOMException("Request timed out", "TimeoutError"));
    }, timeoutMs);
  }
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    dispose: () => {
      if (timer) clearTimeout(timer);
      outer?.removeEventListener("abort", onAbort);
    },
  };
}

async function sendFetch(method: HttpMethod, url: string, options: RequestOptions): Promise<Response> {
  const { params: _params, body, headers, signal, timeoutMs = DEFAULT_TIMEOUT_MS, skipAuthRefresh: _skip, ...init } = options;
  const finalHeaders: Record<string, string> = { Accept: "application/json", ...csrfHeaders(method), ...headers };
  let finalBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (isRawBody(body)) {
      finalBody = body;
    } else {
      finalBody = JSON.stringify(body);
      finalHeaders["Content-Type"] ??= "application/json";
    }
  }
  const linked = linkSignal(signal, timeoutMs);
  try {
    return await fetch(url, { ...init, method, headers: finalHeaders, body: finalBody, credentials: "same-origin", signal: linked.signal });
  } catch (error) {
    if (linked.timedOut()) throw timeoutError();
    if (signal?.aborted || isAbortError(error)) throw error;
    throw networkError(error);
  } finally {
    linked.dispose();
  }
}

async function toRaw(response: Response): Promise<RawResponse> {
  const text = await response.text();
  return { status: response.status, header: (name) => response.headers.get(name), text };
}

async function executeRaw(method: HttpMethod, path: string, options: RequestOptions): Promise<Response> {
  const url = buildUrl(path, options.params);
  const allowRefresh = !options.skipAuthRefresh && shouldRefreshFor(url);
  let response = await sendFetch(method, url, options);
  if (response.status === 401 && allowRefresh) {
    const firstRaw = await toRaw(response);
    const firstError = errorFromResponse(firstRaw, safeParse(firstRaw));
    const outcome = await refreshSession();
    if (outcome === "denied") throw firstError;
    if (outcome === "unavailable") throw refreshUnavailableError(firstError);
    response = await sendFetch(method, url, options);
    if (response.status === 401) {
      emitUnauthorized();
    }
  }
  return response;
}

async function request<T>(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<T> {
  const response = await executeRaw(method, path, options);
  const raw = await toRaw(response);
  const body = parseBody(raw);
  if (response.status < 200 || response.status >= 300) throw failed(raw, body);
  return body as T;
}

/** Like `request` but returns the Response untouched (downloads, streams). Throws ApiError on non-2xx. */
async function requestRaw(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<Response> {
  const response = await executeRaw(method, path, options);
  if (!response.ok) {
    const raw = await toRaw(response);
    let body: unknown;
    try {
      body = parseBody(raw);
    } catch {
      body = undefined;
    }
    throw failed(raw, body);
  }
  return response;
}

// ------------------------------------------------------------------ upload

export interface UploadProgress {
  loaded: number;
  total: number;
  /** 0-100, or null when the total size is unknown. */
  percent: number | null;
}

export interface UploadOptions {
  method?: "POST" | "PUT" | "PATCH";
  params?: QueryParams;
  /** Field name used when a bare File/Blob is passed (default "file"). */
  fieldName?: string;
  /** Extra form fields appended when a bare File/Blob is passed. */
  fields?: Record<string, string | Blob>;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
  skipAuthRefresh?: boolean;
}

function sendXhr(method: string, url: string, body: FormData, options: UploadOptions): Promise<RawResponse> {
  return new Promise<RawResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url, true);
    xhr.withCredentials = false; // same-origin requests always carry cookies
    xhr.setRequestHeader("Accept", "application/json");
    for (const [key, value] of Object.entries(csrfHeaders(method))) xhr.setRequestHeader(key, value);

    const onAbortSignal = () => xhr.abort();
    if (options.signal) {
      if (options.signal.aborted) {
        reject(new DOMException("Upload aborted", "AbortError"));
        return;
      }
      options.signal.addEventListener("abort", onAbortSignal, { once: true });
    }
    const cleanup = () => options.signal?.removeEventListener("abort", onAbortSignal);

    if (options.onProgress && xhr.upload) {
      xhr.upload.onprogress = (event) => {
        const total = event.lengthComputable ? event.total : 0;
        options.onProgress?.({
          loaded: event.loaded,
          total,
          percent: total > 0 ? Math.min(100, Math.round((event.loaded / total) * 100)) : null,
        });
      };
    }
    xhr.onload = () => {
      cleanup();
      resolve({ status: xhr.status, header: (name) => xhr.getResponseHeader(name), text: xhr.responseText ?? "" });
    };
    xhr.onerror = () => {
      cleanup();
      reject(networkError(new Error("XHR network error")));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(timeoutError());
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Upload aborted", "AbortError"));
    };
    xhr.send(body);
  });
}

async function upload<T>(path: string, data: FormData | Blob, options: UploadOptions = {}): Promise<T> {
  const method = options.method ?? "POST";
  const url = buildUrl(path, options.params);
  let form: FormData;
  if (data instanceof FormData) {
    form = data;
  } else {
    form = new FormData();
    const filename = data instanceof File ? data.name : "upload";
    form.append(options.fieldName ?? "file", data, filename);
    for (const [key, value] of Object.entries(options.fields ?? {})) form.append(key, value);
  }
  let raw = await sendXhr(method, url, form, options);
  if (raw.status === 401 && !options.skipAuthRefresh && shouldRefreshFor(url)) {
    const firstError = errorFromResponse(raw, safeParse(raw));
    const outcome = await refreshSession();
    if (outcome === "denied") throw firstError;
    if (outcome === "unavailable") throw refreshUnavailableError(firstError);
    raw = await sendXhr(method, url, form, options);
    if (raw.status === 401) emitUnauthorized();
  }
  const body = parseBody(raw);
  if (raw.status < 200 || raw.status >= 300) throw failed(raw, body);
  return body as T;
}

function safeParse(raw: RawResponse): unknown {
  try {
    return parseBody(raw);
  } catch {
    return undefined;
  }
}

// ------------------------------------------------------------------ facade

type BodyOptions = Omit<RequestOptions, "body">;

export const api = {
  get<T>(path: string, options?: BodyOptions): Promise<T> {
    return request<T>("GET", path, options);
  },
  post<T>(path: string, body?: unknown, options?: BodyOptions): Promise<T> {
    return request<T>("POST", path, { ...options, body });
  },
  put<T>(path: string, body?: unknown, options?: BodyOptions): Promise<T> {
    return request<T>("PUT", path, { ...options, body });
  },
  patch<T>(path: string, body?: unknown, options?: BodyOptions): Promise<T> {
    return request<T>("PATCH", path, { ...options, body });
  },
  delete<T = void>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>("DELETE", path, options);
  },
  request,
  raw: requestRaw,
  upload,
  url: buildUrl,
};

export type Api = typeof api;
