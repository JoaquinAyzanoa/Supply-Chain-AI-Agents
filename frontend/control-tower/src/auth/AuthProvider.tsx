import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";

import { api, unwrap } from "@/api/client";
import { atLeast, authStore, type Role, type Session } from "./store";

export interface AuthState {
  session: Session | null;
  isAuthenticated: boolean;
  hasRole: (role: Role) => boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(
    (listener) => authStore.subscribe(listener),
    () => authStore.get(),
    () => null,
  );

  // On boot, confirm the stored token still works (it may have expired overnight).
  useEffect(() => {
    if (!session) return;
    api.GET("/api/auth/me").then((result) => {
      if (result.response.status === 401) authStore.clear();
      else if (result.data) authStore.set({ token: session.token, user: result.data });
    });
    // Only on mount and when the token changes: the user object comes from this call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  const login = useCallback(async (email: string, password: string) => {
    const data = unwrap(await api.POST("/api/auth/login", { body: { email, password } }));
    authStore.set({ token: data.token, user: { email: data.email, name: data.name, role: data.role } });
  }, []);

  const logout = useCallback(() => authStore.clear(), []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      isAuthenticated: session !== null,
      hasRole: (role) => atLeast(session?.user.role, role),
      login,
      logout,
    }),
    [session, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
