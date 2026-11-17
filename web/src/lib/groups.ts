import { apiRequest } from "@/lib/api";

export interface ImplicitGroupMember {
  channel_model_id: string;
  channel_id: string;
  channel_slug: string;
  channel_name: string;
  protocol: string;
  upstream_model: string;
  call_model: string;
  priority: number;
  enabled: boolean;
  channel_enabled: boolean;
  available: boolean;
}

export interface ImplicitGroup {
  name: string;
  call_model: string;
  member_count: number;
  available_count: number;
  callable: boolean;
  created_at: number;
  members: ImplicitGroupMember[];
}

export interface ImplicitGroupList {
  items: ImplicitGroup[];
  total: number;
}

export async function listImplicitGroups(): Promise<ImplicitGroupList> {
  return apiRequest<ImplicitGroupList>("/api/groups");
}
