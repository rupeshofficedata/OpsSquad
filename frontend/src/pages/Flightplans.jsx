import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";

export default function Flightplans() {
  const [plans, setPlans] = useState([]);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.listFlightplans().then(setPlans).catch((e) => setError(e.message));
  }, []);

  async function handleExecute(slug) {
    try {
      const result = await api.executeFlightplan(slug, {});
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Flightplans</h1>
      {error && <p className="text-red-400">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {plans.map((p) => (
          <div key={p.id} className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-medium">{p.name}</h2>
              <span className="rounded bg-slate-800 px-2 py-0.5 text-xs uppercase text-slate-400">{p.env}</span>
            </div>
            <p className="mt-1 text-sm text-slate-400">{p.description}</p>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => handleExecute(p.slug)}
                className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500"
              >
                Execute
              </button>
              <Link
                to="/runs"
                className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
              >
                History
              </Link>
            </div>
          </div>
        ))}
        {plans.length === 0 && !error && <p className="text-slate-500">No Flightplans yet.</p>}
      </div>
    </div>
  );
}
