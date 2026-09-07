import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@opssquad.dev");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-4 rounded-xl bg-slate-900 p-8 shadow-xl">
        <h1 className="text-2xl font-semibold">🛠️ OpsSquad</h1>
        <p className="text-sm text-slate-400">Agentic DevOps automation</p>

        <div>
          <label className="mb-1 block text-sm text-slate-400">Email</label>
          <input
            className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 outline-none focus:border-indigo-500"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            required
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-400">Password</label>
          <input
            className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 outline-none focus:border-indigo-500"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            required
          />
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-indigo-600 py-2 font-medium hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-xs text-slate-500">
          Seeded users: admin@opssquad.dev / Admin@123 · dev@opssquad.dev / Dev@123 · viewer@opssquad.dev / Viewer@123
        </p>
      </form>
    </div>
  );
}
