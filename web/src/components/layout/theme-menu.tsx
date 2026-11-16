import { ChevronDown, Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useThemeMode, type ThemeMode } from "@/lib/theme";

const THEME_OPTIONS: Array<{
  value: ThemeMode;
  label: string;
  hint: string;
  icon: typeof Monitor;
}> = [
  { value: "system", label: "跟随系统", hint: "随操作系统切换亮暗", icon: Monitor },
  { value: "light", label: "浅色", hint: "始终使用浅色界面", icon: Sun },
  { value: "dark", label: "深色", hint: "始终使用深色界面", icon: Moon },
];

export function ThemeMenu({ compact = false }: { compact?: boolean }) {
  const { mode, dark, setMode } = useThemeMode();
  const current = THEME_OPTIONS.find((option) => option.value === mode) ?? THEME_OPTIONS[0];
  const CurrentIcon = dark ? Moon : current.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? "icon" : "md"}
          className={compact ? undefined : "gap-1.5 px-2.5"}
          aria-label={`切换主题，当前：${current.label}`}
        >
          <CurrentIcon className="h-4 w-4 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          {compact ? null : (
            <>
              <span className="hidden text-xs font-medium sm:inline">{current.label}</span>
              <ChevronDown className="h-3 w-3 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuLabel>主题</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={mode} onValueChange={(value) => setMode(value as ThemeMode)}>
          {THEME_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <DropdownMenuRadioItem key={option.value} value={option.value} className="items-start gap-2.5 py-2">
                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-medium text-title">{option.label}</span>
                  <span className="mt-0.5 block text-2xs text-muted-foreground">{option.hint}</span>
                </span>
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
