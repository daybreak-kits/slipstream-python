import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  apiRequest,
  clearStoredToken,
  getStoredToken,
  setUnauthorizedHandler,
  storeToken,
  type Admin,
  type LoginResponse,
} from "@/lib/api";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  admin: Admin | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<Admin>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [status, setStatus] = useState<AuthStatus>(() => (getStoredToken() ? "loading" : "anonymous"));

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setStatus("anonymous");
      return;
    }

    let cancelled = false;
    apiRequest<Admin>("/api/auth/me")
      .then((current) => {
        if (cancelled) return;
        setAdmin(current);
        setStatus("authenticated");
      })
      .catch(() => {
        if (cancelled) return;
        clearStoredToken();
        setAdmin(null);
        setStatus("anonymous");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAdmin(null);
      setStatus("anonymous");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiRequest<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    storeToken(result.access_token);
    setAdmin(result.admin);
    setStatus("authenticated");
    return result.admin;
  }, []);

  const logout = useCallback(() => {
    clearStoredToken();
    setAdmin(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ admin, status, login, logout }),
    [admin, status, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
