import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

export default function Chat() {
  const [prompt, setPrompt] = useState("");
  const [env, setEnv] = useState("staging");
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);

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
        <select
          value={env}
          onChange={(e) => setEnv(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
        >
          <option value="staging">staging</option>
          <option value="prod">prod</option>
        </select>
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
