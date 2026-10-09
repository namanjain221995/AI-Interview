import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/DropdownMenu";
import { isThemePreference, useTheme, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{ value: ThemePreference; label: string; icon: typeof Sun }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

const LABELS: Record<ThemePreference, string> = { light: "Light", dark: "Dark", system: "System" };

export interface ThemeToggleProps {
  className?: string;
  /** Menu alignment against the trigger. */
  align?: "start" | "center" | "end";
}

/**
 * Header control for the colour theme: an icon button (sun or moon, the theme in use)
 * opening Light / Dark / System. Test ids: `theme-toggle` (carries data-theme-preference
 * and data-resolved-theme) and `theme-option-<light|dark|system>`.
 */
export function ThemeToggle({ className, align = "end" }: ThemeToggleProps) {
  const { preference, resolvedTheme, setPreference } = useTheme();
  const Icon = resolvedTheme === "dark" ? Moon : Sun;
  const label = preference === "system" ? `Theme: System (${resolvedTheme === "dark" ? "dark" : "light"})` : `Theme: ${LABELS[preference]}`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="md"
          aria-label={label}
          data-testid="theme-toggle"
          data-theme-preference={preference}
          data-resolved-theme={resolvedTheme}
          className={cn("size-9 shrink-0 px-0 data-[state=open]:bg-surface-hover data-[state=open]:text-fg", className)}
        >
          <Icon aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="min-w-44">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={preference} onValueChange={(value) => isThemePreference(value) && setPreference(value)}>
          {OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            return (
              <DropdownMenuRadioItem key={option.value} value={option.value} data-testid={`theme-option-${option.value}`}>
                <OptionIcon aria-hidden="true" />
                {option.label}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
