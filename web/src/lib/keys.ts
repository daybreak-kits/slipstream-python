import { apiRequest } from "@/lib/api";

export interface ApiKey {
  id: string;
  label: string;
  prefix: string;
  enabled: boolean;
  last_used_at: number | null;
  created_at: number;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface ApiKeyList {
  items: ApiKey[];
  total: number;
}

export async function listKeys(): Promise<ApiKeyList> {
  return apiRequest<ApiKeyList>("/api/keys");
}

export async function createKey(label: string): Promise<ApiKeyCreated> {
  return apiRequest<ApiKeyCreated>("/api/keys", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export async function updateKey(
  id: string,
  payload: { label?: string; enabled?: boolean },
): Promise<ApiKey> {
  return apiRequest<ApiKey>(`/api/keys/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function revealKey(id: string): Promise<{ key: string }> {
  return apiRequest<{ key: string }>(`/api/keys/${id}/secret`);
}

export async function resetKey(id: string): Promise<ApiKeyCreated> {
  return apiRequest<ApiKeyCreated>(`/api/keys/${id}/reset`, { method: "POST" });
}

export async function deleteKey(id: string): Promise<void> {
  await apiRequest<void>(`/api/keys/${id}`, { method: "DELETE" });
}
