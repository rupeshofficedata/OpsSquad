import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";

const STATUS_DOT = { running: "bg-emerald-500", loading: "bg-amber-500", stopped: "bg-red-500" };

export default function Chat() {
  const { user, updateUser } = useAuth();
  const [prompt, setPrompt] = useState("");
  const [env, setEnv] = useState("staging");
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelNameDraft, setModelNameDraft] = useState(user.model_name || "");
  const [modelStatus, setModelStatus] = useState(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (user.model_provider !== "local") return;
    let cancelled = false;
    const poll = () => api.getModelStatus().then((r) => { if (!cancelled) setModelStatus(r.state); }).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [user.model_provider]);

  async function handleStartModel() {
    setStarting(true);
    try {
      const r = await api.startModel();
      setModelStatus(r.state);
    } finally {
      setStarting(false);
    }
  }

  async function handleProviderChange(provider) {
    setModelSaving(true);
    try {
      updateUser(await api.updateModelPreference(provider, provider === "local" ? modelNameDraft : null));
    } finally {
      setModelSaving(false);
    }
  }

  async function handleModelNameBlur() {
    if (user.model_provider !== "local") return;
    setModelSaving(true);
    try {
      updateUser(await api.updateModelPreference("local", modelNameDraft || null));
    } finally {
      setModelSaving(false);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    const mine = { role: "user", text: prompt };
    setHistory((h) => [...h, mine]);
    setPrompt("");
    try {
      const result = await api.chat(mine.text, env);
      setHistory((h) => [...h, { role: "agent", result }]);
    } catch (err) {
      setHistory((h) => [...h, { role: "error", text: err.message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Chat</h1>
        <div className="flex items-center gap-2">
          <select
            value={user.model_provider}
            onChange={(e) => handleProviderChange(e.target.value)}
            disabled={modelSaving}
            title="Which model your agent runs execute against"
            className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
          >
            <option value="anthropic">Claude (Anthropic API)</option>
            <option value="local">Local (llama.cpp)</option>
          </select>
          {user.model_provider === "local" && (
            <>
              <input
                value={modelNameDraft}
                onChange={(e) => setModelNameDraft(e.target.value)}
                onBlur={handleModelNameBlur}
                placeholder="model label, e.g. qwen2.5-coder:7b"
                className="w-44 rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
              />
              <span
                className={`h-2.5 w-2.5 rounded-full ${STATUS_DOT[modelStatus] || "bg-slate-600"}`}
                title={`model: ${modelStatus || "unknown"}`}
              />
              <span className="text-xs text-slate-400">{modelStatus || "unknown"}</span>
              <button
                onClick={handleStartModel}
                disabled={modelStatus !== "stopped" || starting}
                className="rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800 disabled:opacity-40"
              >
                {starting ? "Starting…" : "Start model"}
              </button>
            </>
          )}
          <select
            value={env}
            onChange={(e) => setEnv(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
          >
            <option value="staging">staging</option>
            <option value="prod">prod</option>
          </select>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        {history.length === 0 && (
          <p className="text-slate-500">
            Try: "scan the payments-api image for critical CVEs" or "what's the current cluster cost?"
          </p>
        )}
        {history.map((m, i) => (
          <div key={i} className="space-y-1">
            {m.role === "user" && <p className="font-medium text-indigo-300">You: {m.text}</p>}
            {m.role === "error" && <p className="text-red-400">Error: {m.text}</p>}
            {m.role === "agent" && (
              <div className="rounded-md bg-slate-800 p-3 text-sm">
                <p className="mb-1 text-slate-400">
                  🧭 Matched agent → <span className="text-slate-200">{m.result.agent}</span>
                  {m.result.run_id && (
                    <>
                      {" · "}
                      <Link to={`/runs/${m.result.run_id}`} className="text-indigo-400 hover:underline">
                        view run
                      </Link>
                    </>
                  )}
                </p>
                {m.result.status === "awaiting_approval" ? (
                  <p className="text-amber-400">
                    This is a mutating action in prod — awaiting admin approval.
                  </p>
                ) : (
                  <>
                    <pre className="whitespace-pre-wrap text-slate-300">
                      {JSON.stringify(m.result.output, null, 2)}
                    </pre>
                    {m.result.reasoning && (
                      <p className="mt-2 text-xs text-slate-500">Reasoning: {m.result.reasoning}</p>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask an agent to do something…"
          className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 outline-none focus:border-indigo-500"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "Running…" : "Send"}
        </button>
      </form>
    </div>
  );
}
