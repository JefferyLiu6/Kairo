import { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from "react";
import { authMe, authLogin, authSignup, authLogout, authCsrf, authDemo, AUTH_REQUIRED_EVENT } from "../api";

export type AuthUser = {
  id: string;
  email: string;
  displayName: string;
  isDemo: boolean;
  creditsRemaining: number;
};

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string, code?: string) => Promise<void>;
  signup: (email: string, password: string, displayName: string) => Promise<void>;
  tryDemo: () => Promise<void>;
  refreshUser: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const demoAttempt = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let active = true;
    const requireLogin = () => {
      if (active) setUser(null);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    authMe()
      .then(async (u) => {
        await authCsrf();
        if (active) setUser(u);
      })
      .catch(() => { if (active) setUser(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => {
      active = false;
      window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    };
  }, []);

  const login = useCallback(async (email: string, password: string, code = "") => {
    const u = await authLogin(email, password, code);
    await authCsrf();
    setUser(u);
  }, []);

  const signup = useCallback(async (email: string, password: string, displayName: string) => {
    await authSignup(email, password, displayName);
    // No auto-login after signup — user must sign in separately.
    // This keeps signup non-enumerating: the server returns 200 regardless of email existence.
  }, []);

  const tryDemo = useCallback(() => {
    // StrictMode and repeated clicks must share one provisioning request.
    if (!demoAttempt.current) {
      demoAttempt.current = (async () => {
        const u = await authDemo();
        await authCsrf();
        setUser(u);
      })().finally(() => { demoAttempt.current = null; });
    }
    return demoAttempt.current;
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const u = await authMe();
      setUser(u);
    } catch (err) {
      // Session rejected (e.g. demo account expired) — return to sign-in.
      // Transient/network errors
      // keep the current user so a blip doesn't discard a live session.
      if (err instanceof Error && (err.message === "401" || err.message === "403")) {
        setUser(null);
      }
    }
  }, []);

  const logout = useCallback(async () => {
    await authLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, tryDemo, refreshUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
