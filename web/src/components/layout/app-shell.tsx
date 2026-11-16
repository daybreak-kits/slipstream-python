import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { readValue, writeValue } from "@/lib/storage";

const env = (import.meta.env.VITE_APP_ENV as string | undefined) ?? "local";

export function AppShell() {
  const [navOpen, setNavOpen] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(() => readValue<boolean>("navCollapsed") === true);
  const location = useLocation();

  useEffect(() => {
    writeValue("navCollapsed", navCollapsed);
  }, [navCollapsed]);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className="flex h-screen overflow-hidden bg-bg">
      <div className="hidden shrink-0 lg:block">
        <Sidebar env={env} collapsed={navCollapsed} />
      </div>

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        {/* Sheet 自带的关闭按钮与 Sidebar 头部的关闭按钮会重叠，这里只保留后者；
            点击遮罩与 Esc 关闭仍然由 Radix 负责。 */}
        <SheetContent
          side="left"
          showCloseButton={false}
          className="w-sidebar max-w-sidebar gap-0 border-r border-border p-0 lg:hidden"
        >
          <SheetTitle className="sr-only">主导航</SheetTitle>
          <SheetDescription className="sr-only">站点主导航，选择页面后自动关闭。</SheetDescription>
          <Sidebar env={env} onClose={() => setNavOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          env={env}
          onOpenNav={() => setNavOpen(true)}
          navCollapsed={navCollapsed}
          onToggleNavCollapsed={() => setNavCollapsed((value) => !value)}
        />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-480 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
