/**
 * Reviewer choices for assignment and filters. The staff directory (`GET /users`)
 * needs `users:manage`, so recruiters get themselves plus every reviewer already seen
 * on intake rows; administrators get the whole active staff list.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";
import { useCurrentUser, usePermission } from "@/lib/auth";
import { queryKeys } from "@/lib/query";

import type { AdminUser } from "@/features/recruiter/admin/types";
import { asPage } from "@/features/recruiter/admin/lib/utils";

import type { PersonRef } from "../types";

export interface ReviewerOption {
  value: string;
  label: string;
  description?: string;
}

const DIRECTORY_PARAMS = { is_active: true, limit: 200, offset: 0 };

export function useReviewerOptions(seen: ReadonlyArray<PersonRef | null | undefined> = []): ReviewerOption[] {
  const me = useCurrentUser();
  const canListUsers = usePermission("users:manage");
  const users = useQuery({
    queryKey: queryKeys.admin.users(DIRECTORY_PARAMS),
    queryFn: async ({ signal }) => asPage<AdminUser>(await api.get<unknown>("/users", { params: DIRECTORY_PARAMS, signal })),
    enabled: canListUsers,
    staleTime: 5 * 60_000,
    meta: { errorToast: false },
  });
  const seenKey = seen.map((p) => `${p?.id ?? ""}:${p?.full_name ?? ""}`).join("|");

  return useMemo(() => {
    const byId = new Map<string, ReviewerOption>();
    const add = (id: string | undefined | null, name: string | undefined | null) => {
      if (!id || byId.has(id)) return;
      byId.set(id, { value: id, label: name?.trim() || "Unnamed reviewer" });
    };
    for (const user of users.data?.items ?? []) add(user.id, user.full_name || user.email);
    for (const entry of seenKey.split("|")) {
      const [id, ...name] = entry.split(":");
      add(id, name.join(":"));
    }
    const others = [...byId.values()].filter((o) => o.value !== me?.id).sort((a, b) => a.label.localeCompare(b.label));
    if (!me) return others;
    const myName = me.full_name && !me.full_name.includes("@") ? `${me.full_name} (me)` : "Me";
    return [{ value: me.id, label: myName, description: me.email }, ...others];
  }, [me, users.data, seenKey]);
}
