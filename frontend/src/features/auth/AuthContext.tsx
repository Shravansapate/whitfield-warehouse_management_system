import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, AUTH_EXPIRED_EVENT, authApi, clearTokens, getAccessToken, storeTokens } from "../../lib/api/client";
import type { UserProfile } from "../../types/wms";

type AuthStatus = "checking" | "anonymous" | "authenticated";

interface AuthContextValue {
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  status: AuthStatus;
  user: UserProfile | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [user, setUser] = useState<UserProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const becomeAnonymous = useCallback(() => {
    clearTokens();
    setUser(null);
    setStatus("anonymous");
  }, []);

  useEffect(() => {
    const handleExpired = () => becomeAnonymous();
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    if (!getAccessToken()) {
      setStatus("anonymous");
      return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    }
    authApi.me()
      .then((profile) => {
        if (!profile.isActive) throw new ApiError("This user account is disabled.", 403);
        setUser(profile);
        setStatus("authenticated");
      })
      .catch(() => becomeAnonymous());
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
  }, [becomeAnonymous]);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const tokens = await authApi.login(email, password);
      storeTokens(tokens.accessToken, tokens.refreshToken);
      const profile = tokens.user ?? await authApi.me();
      if (!profile.isActive) throw new ApiError("This user account is disabled.", 403);
      setUser(profile);
      setStatus("authenticated");
    } catch (caught) {
      becomeAnonymous();
      const message = caught instanceof ApiError ? caught.message : "Sign-in failed. Please try again.";
      setError(message);
      throw caught;
    }
  }, [becomeAnonymous]);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      becomeAnonymous();
    }
  }, [becomeAnonymous]);

  const value = useMemo(() => ({ error, login, logout, status, user }), [error, login, logout, status, user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
