/** Users (`users:manage`), organization settings (`settings:manage`) and voices. */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, isApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { OrganizationSettings, StaffRole, UserCreate, UserUpdate } from "@/lib/types";

import type { AdminUser, VoiceOption } from "../types";
import { asList, asPage } from "../lib/utils";
import { withSettingsDefaults } from "../settings/settingsModel";
import { KOKORO_VOICES } from "../settings/voices";

// -------------------------------------------------------------------- users

export interface UserListQuery {
  q?: string;
  role?: StaffRole;
  is_active?: boolean;
  limit: number;
  offset: number;
}

export function useUsers(params: UserListQuery) {
  return useQuery({
    queryKey: queryKeys.admin.users(params),
    queryFn: async () => asPage<AdminUser>(await api.get<unknown>("/users", { params: { ...params } })),
    placeholderData: keepPreviousData,
  });
}

/** Active admins of the organization (for the "last administrator" guard). */
export function useActiveAdminCount() {
  return useQuery({
    queryKey: queryKeys.admin.users({ role: "ADMIN", is_active: true, countOnly: true }),
    queryFn: async () => asPage<AdminUser>(await api.get<unknown>("/users", { params: { role: "ADMIN", is_active: true, limit: 1 } })).total,
    staleTime: 30_000,
  });
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UserCreate) => api.post<AdminUser>("/users", input),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UserUpdate & { password?: string } }) => api.patch<AdminUser>(`/users/${id}`, input),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useDeactivateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<AdminUser>(`/users/${id}/deactivate`),
    meta: { successToast: "User deactivated" },
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useUnlockUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<AdminUser>(`/users/${id}/unlock`),
    meta: { successToast: "Sign-in lockout cleared" },
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

// ----------------------------------------------------------------- settings

export async function fetchSettings(): Promise<OrganizationSettings> {
  return withSettingsDefaults(await api.get<Partial<OrganizationSettings>>("/settings"));
}

/** Organization defaults (readable by every staff member; shape the job editor too). */
export function useOrgSettings() {
  return useQuery({ queryKey: queryKeys.admin.settings, queryFn: fetchSettings, staleTime: 60_000 });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (settings: OrganizationSettings) => api.put<OrganizationSettings>("/settings", settings),
    meta: { silentError: true },
    onSuccess: (saved) => client.setQueryData(queryKeys.admin.settings, withSettingsDefaults(saved)),
  });
}

// ------------------------------------------------------------------- voices

/**
 * Interviewer voices. Uses `GET /settings/voices` when the server offers it and
 * falls back to the Kokoro catalogue bundled with the app.
 */
export function useVoices() {
  return useQuery({
    queryKey: ["admin", "voices"],
    staleTime: 30 * 60_000,
    retry: false,
    queryFn: async (): Promise<{ voices: VoiceOption[]; live: boolean }> => {
      try {
        const raw = await api.get<unknown>("/settings/voices");
        const voices = asList<VoiceOption>(raw, "voices").filter((v) => typeof v.id === "string");
        return voices.length ? { voices, live: true } : { voices: KOKORO_VOICES, live: false };
      } catch {
        return { voices: KOKORO_VOICES, live: false };
      }
    },
  });
}

export class VoicePreviewUnavailable extends Error {
  constructor() {
    super("Voice preview isn't available on this server yet.");
  }
}

/** Synthesises a short sample with the chosen voice; resolves to an audio Blob. */
export async function fetchVoicePreview(voice: string, text: string, signal?: AbortSignal): Promise<Blob> {
  try {
    const response = await api.raw("POST", "/settings/voice-preview", { body: { voice, text }, signal, timeoutMs: 20_000, headers: { Accept: "audio/*" } });
    return await response.blob();
  } catch (error) {
    if (isApiError(error) && (error.status === 404 || error.status === 405 || error.status === 501)) throw new VoicePreviewUnavailable();
    throw error;
  }
}
