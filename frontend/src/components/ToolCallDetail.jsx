// Renders one tool call's real input/result as labeled key:value pairs
// instead of a raw JSON.stringify blob — used by RunDetail.jsx and
// Chat.jsx's LiveSteps for every tool call that isn't already covered by a
// dedicated card (ResourceCards for kubectl.get pods/services). Arrays of
// plain values (e.g. git.log's commit list) are joined; arrays of objects
// (e.g. trivy's cves, secrets.scan's secrets_found) are compacted to a
// count — showing every CVE/finding inline would need a per-tool renderer,
// out of scope here, and the run's LLM narration (see executor.py's
// summarize_run) already surfaces the notable ones in prose.
function formatValue(v) {
  if (v == null) return "—";
  if (Array.isArray(v)) {
    if (v.length === 0) return "none";
    if (v.every((x) => typeof x !== "object")) return v.join(", ");
    return `${v.length} item${v.length === 1 ? "" : "s"}`;
  }
  if (typeof v === "object") return null;
  return String(v);
}

// One-line gist for the collapsed <summary> — the first entry (or the
// error), not the full breakdown that's already available on expand.
function resultGist(ok, error, entries) {
  if (!ok) return error ? String(error).slice(0, 60) : "failed";
  if (entries.length === 0) return "done";
  const [k, v] = entries[0];
  const val = formatValue(v);
  const gist = val !== null ? `${k.replace(/_/g, " ")}: ${val}` : "done";
  return entries.length > 1 ? `${gist}, +${entries.length - 1} more` : gist;
}

export default function ToolCallDetail({ tc }) {
  const ok = tc.result?.ok !== false;
  const data = tc.result?.data;
  const entries = data && typeof data === "object" && !Array.isArray(data)
    ? Object.entries(data).filter(([k]) => k !== "real" && k !== "items")
    : [];

  return (
    <details className="rounded border border-slate-700 bg-slate-950/50 px-2 py-1.5 text-xs">
      <summary className="cursor-pointer list-none">
        {ok ? "✅" : "❌"} <span className="font-mono text-indigo-300">{tc.tool}</span>
        {Object.keys(tc.input || {}).length > 0 && (
          <span className="text-slate-500"> ({Object.entries(tc.input).map(([k, v]) => `${k}=${v}`).join(", ")})</span>
        )}
        <span className="text-slate-500"> — {resultGist(ok, tc.result?.error, entries)}</span>
      </summary>
      {!ok && <div className="mt-1 text-red-400">{tc.result?.error}</div>}
      {ok && entries.length > 0 && (
        <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
          {entries.map(([k, v]) => {
            const val = formatValue(v);
            return val !== null ? (
              <div key={k}><span className="text-slate-500">{k.replace(/_/g, " ")}: </span><span className="text-slate-300">{val}</span></div>
            ) : null;
          })}
        </div>
      )}
    </details>
  );
}
