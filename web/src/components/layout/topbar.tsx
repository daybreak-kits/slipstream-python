import {
  ChevronDown,
  ChevronRight,
  KeyRound,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  UserRound,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

import { GlobalSearch } from "@/components/layout/global-search";
import { ThemeMenu } from "@/components/layout/theme-menu";
import { TimezoneToggle } from "@/components/layout/timezone-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";
import { findNavGroup, findNavItem } from "@/lib/navigation";
import { cn } from "@/lib/utils";

interface TopbarProps {
  env: string;
  onOpenNav: () => void;
  navCollapsed?: boolean;
  onToggleNavCollapsed?: () => void;
}

export function Topbar({
  env,
  onOpenNav,
  navCollapsed = false,
  onToggleNavCollapsed,
}: TopbarProps) {
  const { admin, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const group = findNavGroup(location.pathname);
  const item = findNavItem(location.pathname);
  const currentLabel = item?.label ?? "管理台";
  const initial = (admin?.username ?? "A").slice(0, 1).toUpperCase();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <header className="relative z-30 flex h-16 shrink-0 items-center gap-3 border-b border-border bg-surface px-4 lg:px-6">
      <Button
        variant="outline"
        size="icon"
        onClick={onOpenNav}
        className="lg:hidden"
        aria-label="打开导航"
      >
        <Menu className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </Button>

      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleNavCollapsed}
        className="hidden lg:inline-flex"
        aria-label={navCollapsed ? "展开导航栏" : "收起导航栏"}
      >
        {navCollapsed ? (
          <PanelLeftOpen className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <PanelLeftClose className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        )}
      </Button>

      <nav className="flex min-w-0 items-center gap-1.5 text-2sm" aria-label="面包屑">
        {group ? <span className="hidden text-muted-foreground sm:inline">{group.label}</span> : null}
        {group ? (
          <ChevronRight className="hidden h-3.5 w-3.5 text-muted-foreground sm:inline" strokeWidth={1.75} aria-hidden="true" />
        ) : null}
        <span className="truncate font-medium text-title">{currentLabel}</span>
      </nav>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <GlobalSearch />

        <TimezoneToggle />

        <Badge
          variant={env === "prod" ? "warning" : "success"}
          className="hidden gap-1.5 px-2 py-1 text-2xs uppercase tracking-[0.08em] sm:inline-flex"
          title={`运行环境：${env}`}
        >
          <span
            className={cn("h-1.5 w-1.5 rounded-full", env === "prod" ? "bg-warning" : "bg-success")}
            aria-hidden="true"
          />
          {env}
        </Badge>

        <ThemeMenu compact />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="gap-2 pl-1 pr-2"
              aria-label={`账号菜单：${admin?.username ?? "管理员"}`}
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-soft text-2xs font-semibold text-primary">
                {initial}
              </span>
              <span className="hidden max-w-30 truncate text-xs font-medium text-title sm:inline">
                {admin?.username ?? "管理员"}
              </span>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <p className="truncate text-2sm font-medium text-title">{admin?.username ?? "管理员"}</p>
              <p className="mt-0.5 flex items-center gap-1 text-2xs text-muted-foreground">
                <UserRound className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                管理员
              </p>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigate("/settings")}>
              <Settings className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              系统设置
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigate("/settings#admin-account")}>
              <KeyRound className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              修改密码
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={handleLogout}
              className="text-danger-text focus:bg-danger-soft focus:text-danger-text"
            >
              <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
