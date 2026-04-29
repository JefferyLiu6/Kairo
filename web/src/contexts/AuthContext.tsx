import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from "react";
import { authMe, authLogin, authSignup, authLogout, authCsrf, authDemo } from "../api";

export type AuthUser = {
  id: string;
  email: string;
  displayName: string;
  isDemo: boolean;
};

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, displayName: string) => Promise<void>;
  tryDemo: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    authMe()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    authCsrf().catch(() => {});
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await authLogin(email, password);
    await authCsrf();
    setUser(u);
  }, []);

  const signup = useCallback(async (email: string, password: string, displayName: string) => {
    await authSignup(email, password, displayName);
    // No auto-login after signup — user must sign in separately.
    // This keeps signup non-enumerating: the server returns 200 regardless of email existence.
  }, []);

  const tryDemo = useCallback(async () => {
    const u = await authDemo();
    await authCsrf();
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    await authLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, tryDemo, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
