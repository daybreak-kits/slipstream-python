import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/lib/auth";

function FullPageLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg" aria-busy="true">
      <div className="flex flex-col items-center gap-3">
        <span className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-on shadow-card">
          <span className="text-lg font-semibold tracking-tight">L</span>
          <span className="absolute inset-0 animate-ping rounded-xl border border-primary" />
        </span>
        <span className="text-2sm text-muted-foreground">正在恢复登录状态…</span>
      </div>
    </div>
  );
}

export function ProtectedRoute() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <FullPageLoader />;
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function PublicOnlyRoute() {
  const { status } = useAuth();

  if (status === "loading") return <FullPageLoader />;
  if (status === "authenticated") return <Navigate to="/overview" replace />;
  return <Outlet />;
}
