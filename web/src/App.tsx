import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/app-shell";
import { ProtectedRoute, PublicOnlyRoute } from "@/components/protected-route";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "@/lib/auth";
import { navItems } from "@/lib/navigation";
import { TimezoneProvider } from "@/lib/time";
import { ChannelDetailPage } from "@/pages/channel-detail";
import { ChannelsPage } from "@/pages/channels";
import { DashboardPage } from "@/pages/dashboard";
import { KeysPage } from "@/pages/keys";
import { LoginPage } from "@/pages/login";
import { LogsPage } from "@/pages/logs";
import { CombosRedirect, ModelsPage } from "@/pages/models";
import { OverviewPage } from "@/pages/overview";
import { PagePlaceholder } from "@/pages/page-placeholder";
import { PlaygroundPage } from "@/pages/playground";
import { SettingsPage } from "@/pages/settings";

const IMPLEMENTED_PATHS = new Set([
  "/",
  "/overview",
  "/channels",
  "/channels/new",
  "/catalog",
  "/logs",
  "/keys",
  "/playground",
  "/settings",
]);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<LoginPage />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="channels" element={<ChannelsPage />} />
          <Route path="channels/new" element={<ChannelDetailPage />} />
          <Route path="channels/:channelId" element={<ChannelDetailPage />} />
          <Route path="catalog" element={<ModelsPage />} />
          <Route path="combos" element={<CombosRedirect />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="keys" element={<KeysPage />} />
          <Route path="playground" element={<PlaygroundPage />} />
          <Route path="settings" element={<SettingsPage />} />
          {navItems
            .filter((item) => !IMPLEMENTED_PATHS.has(item.to))
            .map((item) => (
              <Route
                key={item.to}
                path={item.to.replace(/^\//, "")}
                element={
                  <PagePlaceholder
                    title={item.label}
                    description={item.description}
                    icon={item.icon}
                  />
                }
              />
            ))}
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/overview" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <TimezoneProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
          <Toaster />
        </TimezoneProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
