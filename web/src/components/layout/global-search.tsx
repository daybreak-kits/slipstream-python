import { ArrowRight, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { navItems } from "@/lib/navigation";

const ACTIONS = [
  { label: "新建渠道", to: "/channels", keywords: "创建 渠道 新建", state: { create: true } },
  { label: "新建组合", to: "/combos", keywords: "创建 组合 新建", state: { create: true } },
  { label: "创建用户密钥", to: "/keys", keywords: "创建 密钥 key", state: { create: true } },
  { label: "打开模型试验", to: "/playground", keywords: "调试 对话 试验" },
  { label: "查看使用日志", to: "/logs", keywords: "请求 日志 用量" },
];

/**
 * 平台键位如实标注（ui.md §2.4）：Mac 显示 ⌘K，Win·Linux 显示 Ctrl K。
 * 判定优先读 navigator.platform（"MacIntel" 等，iPad 也按 Mac 处理），缺省再回看 userAgent；
 * 无 navigator（SSR/测试环境）按非 Mac 处理，永远不会在渲染期抛错。
 */
export function isApplePlatform(
  nav: { platform?: string; userAgent?: string } | undefined = typeof navigator === "undefined"
    ? undefined
    : navigator,
): boolean {
  if (!nav) return false;
  const platform = (nav.platform ?? "").toLowerCase();
  if (platform) return /mac|ipad|iphone|ipod/.test(platform);
  return /macintosh|mac os x|ipad|iphone|ipod/.test((nav.userAgent ?? "").toLowerCase());
}

export function GlobalSearch() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // 渲染期读一次平台判定：测试里换 navigator.platform 后重渲染即可断言两种键位。
  const shortcutHint = isApplePlatform() ? "⌘K" : "Ctrl K";

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, []);

  const go = (to: string, state?: unknown) => {
    setOpen(false);
    navigate(to, state ? { state } : undefined);
  };

  return (
    <>
      <Button
        variant="outline"
        size="md"
        onClick={() => setOpen(true)}
        className="hidden gap-2 font-normal text-muted-foreground hover:text-body md:inline-flex"
        aria-label="全局搜索"
      >
        <Search className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        <span className="text-xs">搜索</span>
        <kbd className="ml-4 rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
          {shortcutHint}
        </kbd>
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen(true)}
        className="md:hidden"
        aria-label="打开全局搜索"
      >
        <Search className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </Button>

      <CommandDialog open={open} onOpenChange={setOpen}>
        <DialogTitle className="sr-only">全局搜索</DialogTitle>
        <DialogDescription className="sr-only">搜索页面、功能或快捷操作</DialogDescription>
        {/* cmdk 会给输入框挂一个指向隐藏空 <label> 的 aria-labelledby，
            空名字按 accname 规范会继续回落到 aria-label，所以这里只给 aria-label。 */}
        <CommandInput placeholder="搜索页面、功能或快捷操作…" aria-label="搜索页面和操作" />
        <CommandList>
          <CommandEmpty>没有匹配的页面或操作</CommandEmpty>
          <CommandGroup heading="页面">
            {navItems.map((item) => (
              <CommandItem
                key={item.to}
                value={item.label}
                keywords={[item.description]}
                onSelect={() => go(item.to)}
              >
                <item.icon className="h-4 w-4 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-2sm font-medium text-title">{item.label}</span>
                  <span className="block truncate text-2xs text-muted-foreground">{item.description}</span>
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="快捷操作">
            {ACTIONS.map((item) => (
              <CommandItem
                key={item.label}
                value={item.label}
                keywords={item.keywords.split(" ")}
                onSelect={() => go(item.to, item.state)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-2sm font-medium text-title">{item.label}</span>
                  <span className="block truncate text-2xs text-muted-foreground">快捷操作</span>
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </>
  );
}
