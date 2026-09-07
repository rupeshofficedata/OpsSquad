import { createContext, useContext, useEffect, useState } from "react";
import { api, setAccessToken } from "../api/client.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { accessToken } = await api.refresh();
        setAccessToken(accessToken);
        setUser(await api.me());
      } catch {
        // no valid session — user needs to log in
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function login(email, password) {
    const { accessToken, user } = await api.login(email, password);
    setAccessToken(accessToken);
    setUser(user);
  }

  async function logout() {
    await api.logout().catch(() => {});
    setAccessToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
