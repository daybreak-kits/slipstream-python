import { Navigate, Outlet, useLocation } from "react-router-dom";

import { Spinner } from "@/components/ui/spinner";
import { useAuth } from "@/lib/auth";

function FullPageLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg" aria-busy="true">
      <div className="flex flex-col items-center gap-3">
        {/* 品牌位与侧栏/登录页同源（audit B5）：contrast 版 logo 固定深色底可用，不随主题反转。 */}
        <img
          src="/logo-v10-contrast.svg"
          alt=""
          aria-hidden="true"
          className="h-11 w-11 shrink-0"
        />
        {/* spinner 只作视觉指示，可读文案由下方可见文本承担，避免双重播报。 */}
        <Spinner size="lg" className="text-primary" aria-hidden="true" />
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
