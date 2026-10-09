import {
  BarChart3,
  BriefcaseBusiness,
  ClipboardCheck,
  FileText,
  LayoutDashboard,
  LayoutTemplate,
  LibraryBig,
  Link2,
  MonitorPlay,
  Settings,
  ShieldCheck,
  SquareCode,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { Permission } from "@/lib/types";

export interface StaffNavItem {
  key: string;
  label: string;
  to: string;
  icon: LucideIcon;
  /** Hidden unless the user holds this permission. */
  permission?: Permission;
  /** Extra words the command menu matches. */
  keywords?: string;
}

export interface StaffNavGroup {
  key: string;
  label?: string;
  items: StaffNavItem[];
}

export const STAFF_NAV: StaffNavGroup[] = [
  {
    key: "home",
    items: [{ key: "dashboard", label: "Dashboard", to: "/app/dashboard", icon: LayoutDashboard, keywords: "home overview" }],
  },
  {
    key: "hiring",
    label: "Hiring",
    items: [
      { key: "jobs", label: "Jobs", to: "/app/jobs", icon: BriefcaseBusiness, permission: "jobs:read", keywords: "roles positions openings" },
      { key: "links", label: "Interview links", to: "/app/links", icon: Link2, permission: "jobs:read", keywords: "open link campus drive registration share" },
      { key: "candidates", label: "Candidates", to: "/app/candidates", icon: Users, permission: "candidates:read", keywords: "people applicants resumes" },
      { key: "sessions", label: "Sessions", to: "/app/sessions", icon: MonitorPlay, permission: "sessions:read", keywords: "interviews live" },
      { key: "intake", label: "Intake interviews", to: "/app/intake", icon: ClipboardCheck, permission: "sessions:read", keywords: "intake screening training onboarding" },
      { key: "reports", label: "Reports", to: "/app/reports", icon: FileText, permission: "reports:read", keywords: "results evaluations" },
      { key: "integrity", label: "Integrity review", to: "/app/integrity", icon: ShieldCheck, permission: "integrity:review", keywords: "flags proctoring" },
    ],
  },
  {
    key: "library",
    label: "Library",
    items: [
      { key: "question-bank", label: "Question bank", to: "/app/question-bank", icon: LibraryBig, permission: "jobs:read", keywords: "questions" },
      { key: "coding-questions", label: "Coding questions", to: "/app/coding-questions", icon: SquareCode, permission: "jobs:read", keywords: "problems exercises code" },
      { key: "templates", label: "Templates", to: "/app/templates", icon: LayoutTemplate, permission: "jobs:read", keywords: "rounds" },
    ],
  },
  {
    key: "insights",
    label: "Insights",
    items: [{ key: "analytics", label: "Analytics", to: "/app/analytics", icon: BarChart3, permission: "analytics:read", keywords: "metrics funnel" }],
  },
  {
    key: "admin",
    label: "Admin",
    items: [
      { key: "settings", label: "Settings", to: "/app/settings", icon: Settings, permission: "settings:manage", keywords: "organization policy retention" },
      { key: "users", label: "Users", to: "/app/users", icon: UserCog, permission: "users:manage", keywords: "team members access" },
    ],
  },
];

export function visibleNav(permissions: readonly Permission[]): StaffNavGroup[] {
  return STAFF_NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.permission || permissions.includes(item.permission)),
  })).filter((group) => group.items.length > 0);
}
