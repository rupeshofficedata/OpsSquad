import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

const STATUS_COLOR = {
  success: "text-emerald-400",
  failed: "text-red-400",
  running: "text-amber-400",
  queued: "text-slate-400",
  awaiting_approval: "text-indigo-400",
  awaiting_user_input: "text-amber-400",
  aborted: "text-slate-500",
};

export default function Dashboard() {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listRuns().then(setRuns).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Run History</h1>
      {error && <p className="text-red-400">{error}</p>}

      <div className="overflow-hidden rounded-lg border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-left text-slate-400">
            <tr>
              <th className="px-4 py-2">Kind</th>
              <th className="px-4 py-2">Agent / Flightplan</th>
              <th className="px-4 py-2">Question</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-slate-800 hover:bg-slate-900/50">
                <td className="px-4 py-2">
                  <Link to={`/runs/${r.id}`} className="text-indigo-400 hover:underline">
                    {r.kind}
                  </Link>
                </td>
                <td className="px-4 py-2">{r.agent_slug || r.flightplan_slug || "—"}</td>
                <td className="max-w-xs truncate px-4 py-2 text-slate-300" title={r.prompt || ""}>{r.prompt || "—"}</td>
                <td className={`px-4 py-2 font-medium ${STATUS_COLOR[r.status] || ""}`}>{r.status}</td>
                <td className="px-4 py-2 text-slate-400">{new Date(r.started_at).toLocaleString()}</td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-500">
                  No runs yet — try the Chat page.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
