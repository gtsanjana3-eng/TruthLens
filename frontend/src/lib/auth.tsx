import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore, type User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, name: string, password: string) => Promise<void>;
  logout: () => void;
  setUser: (u: User) => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function applyPreferences(prefs: Record<string, unknown> | undefined) {
  const root = document.documentElement;
  root.classList.toggle("reduce-motion", !!prefs?.reduced_motion);
  root.classList.toggle("high-contrast", !!prefs?.high_contrast);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!tokenStore.get());

  const setUser = useCallback((u: User) => { setUserState(u); applyPreferences(u.preferences); }, []);
  const logout = useCallback(() => { tokenStore.set(null); setUserState(null); applyPreferences({}); }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout);
    if (!tokenStore.get()) return;
    api.me().then(setUser).catch(logout).finally(() => setLoading(false));
  }, [logout, setUser]);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api.login(email, password);
    tokenStore.set(r.access_token);
    setUser(r.user);
  }, [setUser]);

  const register = useCallback(async (email: string, name: string, password: string) => {
    const r = await api.register(email, name, password);
    tokenStore.set(r.access_token);
    setUser(r.user);
  }, [setUser]);

  const value = useMemo(() => ({ user, loading, login, register, logout, setUser }), [user, loading, login, register, logout, setUser]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
