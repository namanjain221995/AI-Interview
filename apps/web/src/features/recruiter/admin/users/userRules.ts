/**
 * Client-side mirror of the server's user-administration rules
 * (app/core/rbac.py can_assign_role / can_manage_user and
 * app/services/identity/users.py). The server enforces all of them; the UI
 * uses these to hide or explain actions that would be refused.
 */

import type { StaffRole } from "@/lib/types";

export const ROLE_RANK: Record<StaffRole, number> = { INTERVIEWER: 1, RECRUITER: 2, ADMIN: 3, SUPER_ADMIN: 4 };
export const STAFF_ROLE_ORDER: StaffRole[] = ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"];

export const ROLE_DESCRIPTIONS: Record<StaffRole, string> = {
  SUPER_ADMIN: "Every organization, including organization setup",
  ADMIN: "Users, settings and everything recruiters can do",
  RECRUITER: "Jobs, candidates, invitations, reviews and the library",
  INTERVIEWER: "Read sessions and reports, and record review decisions",
};

export interface Actor {
  id: string;
  role: StaffRole;
}

export interface Target {
  id: string;
  role: StaffRole;
  is_active: boolean;
}

/** Roles `actor` may give to a new or existing user. */
export function assignableRoles(actorRole: StaffRole): StaffRole[] {
  if (actorRole === "SUPER_ADMIN") return [...STAFF_ROLE_ORDER];
  if (actorRole === "ADMIN") return STAFF_ROLE_ORDER.filter((r) => ROLE_RANK[r] <= ROLE_RANK.ADMIN);
  return [];
}

export function canManageUser(actor: Actor, target: Target): boolean {
  if (actor.role === "SUPER_ADMIN") return true;
  if (actor.role === "ADMIN") return target.role !== "SUPER_ADMIN";
  return false;
}

export type Denial = { allowed: false; reason: string } | { allowed: true };

const allowed: Denial = { allowed: true };

/** May `actor` move `target` to `nextRole`? (`activeAdmins` counts active ADMINs in the org.) */
export function checkRoleChange(actor: Actor, target: Target, nextRole: StaffRole | null, activeAdmins: number | null): Denial {
  if (!canManageUser(actor, target)) return { allowed: false, reason: "Only a super admin can change a super admin." };
  if (actor.id === target.id) return { allowed: false, reason: "You can't change your own role. Ask another administrator." };
  if (nextRole && !assignableRoles(actor.role).includes(nextRole)) return { allowed: false, reason: "Only a super admin can grant the super admin role." };
  if (nextRole && target.role === "ADMIN" && nextRole !== "ADMIN" && target.is_active && activeAdmins !== null && activeAdmins <= 1) {
    return { allowed: false, reason: "This is the only active administrator. Make someone else an admin first." };
  }
  return allowed;
}

export function checkDeactivate(actor: Actor, target: Target, activeAdmins: number | null): Denial {
  if (!canManageUser(actor, target)) return { allowed: false, reason: "Only a super admin can deactivate a super admin." };
  if (actor.id === target.id) return { allowed: false, reason: "You can't deactivate your own account." };
  if (!target.is_active) return { allowed: false, reason: "Already deactivated." };
  if (target.role === "ADMIN" && activeAdmins !== null && activeAdmins <= 1) {
    return { allowed: false, reason: "This is the only active administrator. Make someone else an admin first." };
  }
  return allowed;
}

export function checkReactivate(actor: Actor, target: Target): Denial {
  if (!canManageUser(actor, target)) return { allowed: false, reason: "Only a super admin can manage a super admin." };
  if (target.is_active) return { allowed: false, reason: "Already active." };
  return allowed;
}

export const PASSWORD_MIN_LENGTH = 12;

/**
 * Early feedback that mirrors the server's NIST-style policy (length, no
 * name/email, not trivially repetitive). The server has the final word,
 * including the common-password list.
 */
export function passwordHints(password: string, context: { email?: string; fullName?: string } = {}): string[] {
  const problems: string[] = [];
  if (password.length < PASSWORD_MIN_LENGTH) problems.push(`At least ${PASSWORD_MIN_LENGTH} characters.`);
  if (password.length > 256) problems.push("At most 256 characters.");
  if (new Set(password).size < 5 || /^(.{1,3})\1+$/.test(password)) problems.push("Less repetitive.");
  const lowered = password.toLowerCase();
  const words: string[] = [];
  const local = context.email?.split("@")[0]?.toLowerCase();
  if (local) {
    words.push(...local.split(/[^a-z0-9]+/).filter((w) => w.length >= 4));
    if (local.length >= 4) words.push(local);
  }
  if (context.fullName) words.push(...context.fullName.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 4));
  if (words.some((w) => lowered.includes(w))) problems.push("Doesn't contain the person's name or email.");
  return problems;
}

/** A random, policy-compliant temporary password (Web Crypto). */
export function generatePassword(length = 18): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_!@#%+=";
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += alphabet[b % alphabet.length];
  return out;
}
