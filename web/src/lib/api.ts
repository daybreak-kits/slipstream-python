import { readValue, removeValue, writeValue } from "@/lib/storage";

export interface Admin {
  id: string;
  username: string;
  created_at: number;
  updated_at: number;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: number;
  admin: Admin;
}

interface ErrorBody {
  code?: string;
  message?: string;
  request_id?: string;
  details?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details: unknown;

  constructor(status: number, body: ErrorBody) {
    super(body.message || `请求失败（HTTP ${status}）`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code || "UNKNOWN";
    this.requestId = body.request_id || "";
    this.details = body.details;
  }
}

export function getStoredToken(): string | null {
  return readValue<string>("token") ?? null;
}

export function storeToken(token: string): void {
  writeValue("token", token);
}

export function clearStoredToken(): void {
  removeValue("token");
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = getStoredToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      clearStoredToken();
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, (body as ErrorBody) || {});
  }

  return body as T;
}
