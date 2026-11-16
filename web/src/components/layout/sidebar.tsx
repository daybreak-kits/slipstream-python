import { X } from "lucide-react";
import { NavLink } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { navGroups } from "@/lib/navigation";
import { cn } from "@/lib/utils";

interface SidebarProps {
  env: string;
  collapsed?: boolean;
  onClose?: () => void;
}

export function Sidebar({ env, collapsed = false, onClose }: SidebarProps) {
  return (
    <div
      className={cn(
        "flex h-full flex-col border-r border-border bg-surface transition-[width] duration-200",
        collapsed ? "w-18" : "w-sidebar",
      )}
    >
      <div
        className={cn(
          "flex h-16 shrink-0 items-center",
          collapsed ? "justify-center px-2" : "justify-between px-5",
        )}
      >
        <div className={cn("flex items-center", collapsed ? "justify-center" : "gap-2.5")}>
          <img
            src="/logo-v10-contrast.svg"
            alt=""
            aria-hidden="true"
            className="h-9 w-9 shrink-0"
            title={collapsed ? "驭流 Slipstream" : undefined}
          />
          <div className={cn("leading-tight", collapsed && "sr-only")}>
            <p className="text-sm font-semibold tracking-tight text-title">驭流</p>
            <p className="text-2xs font-medium uppercase tracking-[0.16em] text-muted-foreground">Slipstream</p>
          </div>
        </div>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            className="lg:hidden"
            aria-label="关闭导航"
          >
            <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      <nav
        className={cn("scrollbar-thin flex-1 overflow-y-auto pb-4", collapsed ? "px-2" : "px-3")}
        aria-label="主导航"
      >
        {navGroups.map((group) => (
          <div key={group.label} className={cn("mt-4 first:mt-1", collapsed && "border-t border-border pt-3 first:border-t-0 first:pt-0")}>
            <p
              className={cn(
                "px-3 pb-1.5 text-2xs font-semibold uppercase tracking-[0.14em] text-muted-foreground",
                collapsed && "sr-only",
              )}
            >
              {group.label}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    onClick={onClose}
                    title={collapsed ? item.label : undefined}
                    aria-label={collapsed ? item.label : undefined}
                    className={({ isActive }) =>
                      cn(
                        "group relative flex items-center rounded-control py-2 text-2sm transition-colors duration-150",
                        collapsed ? "justify-center px-2" : "gap-2.5 px-2.5",
                        isActive
                          ? "bg-primary-soft font-medium text-primary"
                          : "text-body hover:bg-surface-2 hover:text-title",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span
                          className={cn(
                            "flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors",
                            isActive
                              ? "bg-surface text-primary shadow-xs"
                              : "bg-surface-2 text-muted-foreground group-hover:text-title",
                          )}
                        >
                          <Icon className="size-4" strokeWidth={1.75} aria-hidden="true" />
                        </span>
                        <span className={collapsed ? "sr-only" : "truncate"}>{item.label}</span>
                        {isActive ? (
                          <span
                            className={cn(
                              "absolute h-4 w-0.5 rounded-r-full bg-primary",
                              collapsed ? "-left-2" : "-left-3",
                            )}
                            aria-hidden="true"
                          />
                        ) : null}
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className={cn("shrink-0 border-t border-border", collapsed ? "px-2 py-3" : "px-4 py-3")}>
        <div
          className={cn(
            "flex items-center text-2xs text-muted-foreground",
            collapsed ? "justify-center" : "justify-between",
          )}
        >
          {collapsed ? null : <span className="font-mono">v0.1.0</span>}
          <Badge variant="success" className="gap-1.5 font-medium" title={`运行环境：${env}`}>
            <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
            <span className={collapsed ? "sr-only" : undefined}>{env}</span>
          </Badge>
        </div>
      </div>
    </div>
  );
}
