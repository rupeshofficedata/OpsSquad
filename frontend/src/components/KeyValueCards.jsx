// A plain structured output (deterministic agents, e.g. diagnose's
// replicas_ready/recommended_action, or a Flightplan step's raw output)
// rendered as label:value cards instead of a raw JSON dump. Shared by
// Chat.jsx and RunDetail.jsx.
export default function KeyValueCards({ data }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined);
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {entries.map(([k, v]) => (
        <div key={k} className="rounded-md border border-slate-700 bg-slate-900/60 px-2 py-1.5 text-xs">
          <div className="text-slate-500">{k.replace(/_/g, " ")}</div>
          <div className="truncate text-slate-200" title={String(v)}>{Array.isArray(v) ? v.join(", ") || "—" : String(v)}</div>
        </div>
      ))}
    </div>
  );
}
